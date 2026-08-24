# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""
Code Executor — Docker Sandbox for Quick Chat
===============================================

Runs user-generated Python scripts (produced by LLM) in isolated Docker containers.
Files are mounted read-only from S3 download, output is written to a writable volume.

Security:
- Network disabled (network_mode="none") 
- CPU limit: 1 core, Memory: 512MB
- 60-second execution timeout
- Non-root user inside container
- Read-only root filesystem except /workspace/output
- Basic script safety validation
"""

import asyncio
import io
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
import uuid
from pathlib import PurePosixPath
from typing import Awaitable, Callable, Dict, List, Optional, Any

import docker
from docker.errors import ContainerError, ImageNotFound, APIError, DockerException

from bucket import upload_file, generate_download_url, _get_client, get_config, get_environment_prefix
from services.sandbox_file_cache import get_or_download as _cached_s3_download
from services.sandbox_pool import WarmContainerPool

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = os.getenv("QUICK_CHAT_SANDBOX_IMAGE", "quick-chat-sandbox")
EXECUTION_TIMEOUT = int(os.getenv("QUICK_CHAT_SANDBOX_EXEC_TIMEOUT", "60"))  # seconds
MAX_OUTPUT_SIZE = 50 * 1024 * 1024  # 50MB max output
MAX_OUTPUT_FILES = 20  # Max number of output files per execution

# Optional async callback signature: ``await progress_cb({"phase": str, ...})``.
# Used by the SSE relay in streaming_response / quick_chat to surface real-time
# sandbox progress to the UI.
ProgressCallback = Optional[Callable[[Dict[str, Any]], Awaitable[None]]]


# Module-level Docker client (reused across calls)
_docker_client = None


# Patterns that indicate potentially dangerous operations
BLOCKED_PATTERNS = [
    r'\bos\.system\b',
    r'\bsubprocess\b',
    r'\b__import__\b',
    r'\bimportlib\b',
    r'\beval\s*\(',
    r'\bexec\s*\(',
    r'\bcompile\s*\(',
    r'\bopen\s*\([^)]*["\']\/(?!workspace)',  # open() outside /workspace
    r'\bsocket\b',
    r'\burllib\b',
    r'\brequests\b',
    r'\bhttpx\b',
    r'\baiohttp\b',
    r'\bshutil\.rmtree\b',
    r'\bos\.remove\b',
    r'\bos\.unlink\b',
    r'\bctypes\b',
    r'\bos\.environ\b',
    r'\bos\.popen\b',
    r'\bos\.exec',
    r'\bos\.spawn',
    r'\bsignal\b',
    r'\bmultiprocessing\b',
]


def validate_script(script: str) -> Optional[str]:
    """
    Basic safety validation on the script. Returns error message if blocked, None if OK.
    This is defense-in-depth — the Docker sandbox itself is the primary security boundary.
    """
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, script):
            return f"Script contains blocked pattern: {pattern}"
    return None


def _get_docker_client():
    """Get Docker client, reusing the module-level singleton."""
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


def _build_workspace_archive(script_path: str, input_dir: str) -> bytes:
    """
    Package the script and input files into a tar archive for Docker put_archive().

    This avoids bind-mounting container-local temp paths, which breaks when
    Citra-Service itself runs inside Docker and talks to the host daemon.
    """
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w") as tar:
        tar.add(script_path, arcname="script.py", recursive=False)
        if os.path.isdir(input_dir):
            tar.add(input_dir, arcname="input", recursive=True)

    archive_buffer.seek(0)
    return archive_buffer.getvalue()


def _extract_output_archive(container, output_dir: str):
    """Copy /workspace/output from the sandbox container back to a local directory."""
    stream, _ = container.get_archive("/workspace/output")
    archive_buffer = io.BytesIO()

    for chunk in stream:
        archive_buffer.write(chunk)

    archive_buffer.seek(0)
    output_root = os.path.realpath(output_dir)

    with tarfile.open(fileobj=archive_buffer, mode="r:*") as tar:
        for member in tar.getmembers():
            if member.isdir() or member.issym() or member.islnk():
                continue

            parts = [part for part in PurePosixPath(member.name).parts if part not in ("", ".")]
            if not parts:
                continue
            if parts[0] == "output":
                parts = parts[1:]
            if not parts or any(part == ".." for part in parts):
                continue

            destination = os.path.join(output_dir, *parts)
            destination_dir = os.path.dirname(destination)
            os.makedirs(destination_dir, exist_ok=True)

            if not os.path.realpath(destination).startswith(output_root):
                logger.warning(f"⚠️ Skipping suspicious output archive path: {member.name}")
                continue

            file_obj = tar.extractfile(member)
            if file_obj is None:
                continue

            with open(destination, "wb") as f:
                shutil.copyfileobj(file_obj, f)


async def download_s3_files_to_dir(
    s3_keys: List[Dict[str, str]],
    target_dir: str,
    *,
    progress_cb: ProgressCallback = None,
) -> Dict[str, Any]:
    """
    Resolve each ``{filename, s3_key}`` to a local file in ``target_dir``,
    using the host-side disk cache to avoid re-downloading unchanged files.

    Returns a tiny stats dict (cache hits/misses, bytes) for the timing log.
    """
    client = _get_client()
    bucket_name, _ = get_config()
    env_prefix = get_environment_prefix()

    hits = misses = bytes_total = 0

    for file_info in s3_keys:
        s3_key = file_info['s3_key']
        filename = file_info['filename']

        full_key = s3_key
        if not full_key.startswith(f"{env_prefix}/"):
            full_key = f"{env_prefix}/{s3_key.lstrip('/')}"

        local_path = os.path.join(target_dir, os.path.basename(filename))
        # Path traversal guard
        if not os.path.realpath(local_path).startswith(os.path.realpath(target_dir)):
            logger.error(f"❌ Path traversal attempt in filename: {filename}")
            continue

        if progress_cb:
            await progress_cb({"phase": "download", "status": "start", "filename": filename})

        t0 = time.perf_counter()
        try:
            cache_path, size, hit = await _cached_s3_download(client, bucket_name, full_key)
        except Exception as e:
            logger.error(f"❌ Failed to fetch {full_key}: {e}")
            if progress_cb:
                await progress_cb({"phase": "download", "status": "error", "filename": filename, "error": str(e)})
            raise

        # Hard-link from the cache into the per-run input dir when possible to
        # avoid copying. Fall back to copy if cross-device.
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
            os.link(cache_path, local_path)
        except OSError:
            shutil.copyfile(cache_path, local_path)

        bytes_total += size
        if hit:
            hits += 1
        else:
            misses += 1

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            f"📥 [SANDBOX] {'cache_hit ' if hit else 'fetched   '}{filename} "
            f"({size} bytes, {elapsed_ms}ms)"
        )
        if progress_cb:
            await progress_cb({
                "phase": "download", "status": "done",
                "filename": filename, "bytes": size,
                "cache_hit": hit, "duration_ms": elapsed_ms,
            })

    return {"hits": hits, "misses": misses, "bytes": bytes_total}


async def execute_code(
    script: str,
    session_id: str,
    files: List[Dict[str, str]],
    output_filename: str = "output.txt",
    *,
    progress_cb: ProgressCallback = None,
) -> Dict[str, Any]:
    """
    Execute a Python script in a Docker sandbox container.
    
    Args:
        script: Python script to execute
        session_id: Quick chat session ID (for S3 output path)
        files: List of file dicts with 'filename' and 's3_key'
        output_filename: Expected output filename
        
    Returns:
        Dict with success, stdout, stderr, output_files (list of {filename, download_url})
    """
    # Validate script safety
    safety_error = validate_script(script)
    if safety_error:
        logger.warning(f"⚠️ [CODE_EXEC] Script blocked: {safety_error}")
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Script validation failed: {safety_error}",
            "output_files": []
        }
    
    exec_id = str(uuid.uuid4())[:8]
    temp_dir = tempfile.mkdtemp(prefix=f"qc_{exec_id}_")
    input_dir = os.path.join(temp_dir, "input")
    output_dir = os.path.join(temp_dir, "output")
    script_path = os.path.join(temp_dir, "script.py")

    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    timings: Dict[str, int] = {}
    overall_t0 = time.perf_counter()

    try:
        # 1. Download / cache raw files from S3
        t0 = time.perf_counter()
        dl_stats: Dict[str, Any] = {"hits": 0, "misses": 0, "bytes": 0}
        if files:
            dl_stats = await download_s3_files_to_dir(
                files, input_dir, progress_cb=progress_cb,
            )
        timings["download_ms"] = int((time.perf_counter() - t0) * 1000)

        # 2. Write script to temp file
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script)

        # 3. Run in a pooled warm container
        logger.info(
            f"🐳 [CODE_EXEC:{exec_id}] Running script in sandbox "
            f"(files={len(files or [])}, dl_hits={dl_stats['hits']}, dl_miss={dl_stats['misses']}, "
            f"download_ms={timings['download_ms']})..."
        )
        if progress_cb:
            await progress_cb({"phase": "exec", "status": "start", "exec_id": exec_id})

        t0 = time.perf_counter()
        result = await _run_in_pooled_container(
            script_path=script_path,
            input_dir=input_dir,
            output_dir=output_dir,
            exec_id=exec_id,
            progress_cb=progress_cb,
        )
        timings["exec_ms"] = int((time.perf_counter() - t0) * 1000)

        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        exit_code = result.get("exit_code", 1)

        logger.info(
            f"🐳 [CODE_EXEC:{exec_id}] Exit={exit_code} exec_ms={timings['exec_ms']} "
            f"stdout={len(stdout)}c stderr={len(stderr)}c"
        )
        if progress_cb:
            await progress_cb({
                "phase": "exec", "status": "done",
                "exec_id": exec_id, "exit_code": exit_code,
                "duration_ms": timings["exec_ms"],
            })
        
        # 4. Collect output files and upload to S3
        upload_t0 = time.perf_counter()
        output_files = []
        if exit_code == 0:
            output_entries = os.listdir(output_dir)
            if len(output_entries) > MAX_OUTPUT_FILES:
                logger.warning(f"⚠️ [CODE_EXEC:{exec_id}] Too many output files ({len(output_entries)}), taking first {MAX_OUTPUT_FILES}")
                output_entries = output_entries[:MAX_OUTPUT_FILES]

            for fname in output_entries:
                # Sanitize output filename (defense-in-depth: container ran as non-root)
                safe_fname = os.path.basename(fname)
                if not safe_fname or safe_fname.startswith('.'):
                    continue
                fpath = os.path.join(output_dir, safe_fname)
                if not os.path.isfile(fpath):
                    continue
                if not os.path.realpath(fpath).startswith(os.path.realpath(output_dir)):
                    logger.warning(f"⚠️ [CODE_EXEC:{exec_id}] Skipping suspicious output path: {fname}")
                    continue

                file_size = os.path.getsize(fpath)
                if file_size > MAX_OUTPUT_SIZE:
                    logger.warning(f"⚠️ [CODE_EXEC:{exec_id}] Output file {safe_fname} too large ({file_size} bytes), skipping")
                    continue
                
                with open(fpath, 'rb') as f:
                    content = f.read()
                
                # Determine content type
                content_type = _guess_content_type(safe_fname)
                s3_key = f"quick-chat/{session_id}/output/{safe_fname}"
                
                upload_file(content, s3_key, content_type)
                download_url = generate_download_url(s3_key, expiry_seconds=1800)
                
                output_files.append({
                    "filename": safe_fname,
                    "download_url": download_url,
                    "size": file_size,
                    "content_type": content_type,
                })
                logger.info(f"📤 [CODE_EXEC:{exec_id}] Uploaded output: {safe_fname} ({file_size} bytes)")

        timings["upload_ms"] = int((time.perf_counter() - upload_t0) * 1000)
        timings["total_ms"] = int((time.perf_counter() - overall_t0) * 1000)
        logger.info(
            f"⏱️  [CODE_EXEC:{exec_id}] timings download={timings['download_ms']}ms "
            f"exec={timings.get('exec_ms', 0)}ms upload={timings['upload_ms']}ms "
            f"total={timings['total_ms']}ms output_files={len(output_files)}"
        )
        if progress_cb:
            await progress_cb({
                "phase": "complete", "status": "done",
                "exec_id": exec_id, "timings": timings,
                "output_files": [{"filename": f["filename"]} for f in output_files],
            })

        return {
            "success": exit_code == 0,
            "stdout": stdout[:10000],  # Cap stdout to 10K chars
            "stderr": stderr[:5000],   # Cap stderr to 5K chars
            "output_files": output_files,
            "timings": timings,
        }
        
    except ImageNotFound:
        logger.error(f"❌ [CODE_EXEC:{exec_id}] Sandbox image '{SANDBOX_IMAGE}' not found. Build it with: docker build -t {SANDBOX_IMAGE} -f Dockerfile.quick-chat-sandbox .")
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Sandbox Docker image '{SANDBOX_IMAGE}' not found. Please build it first.",
            "output_files": []
        }
    except DockerException as e:
        logger.error(f"❌ [CODE_EXEC:{exec_id}] Docker daemon unavailable: {e}", exc_info=True)
        return {
            "success": False,
            "stdout": "",
            "stderr": (
                "Docker daemon unavailable from the Citra-Service container. "
                "If Citra-Service is running in Docker, mount /var/run/docker.sock:/var/run/docker.sock "
                "and ensure the quick-chat-sandbox image is available."
            ),
            "output_files": []
        }
    except Exception as e:
        logger.error(f"❌ [CODE_EXEC:{exec_id}] Error: {e}", exc_info=True)
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "output_files": []
        }
    finally:
        # Cleanup temp directory
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


async def _run_in_pooled_container(
    *,
    script_path: str,
    input_dir: str,
    output_dir: str,
    exec_id: str,
    progress_cb: ProgressCallback = None,
) -> Dict[str, Any]:
    """
    Acquire a warm container, push the workspace, exec the script with
    streaming stdout, pull the output, return container to pool.
    """
    pool = WarmContainerPool.get()

    t_acq = time.perf_counter()
    pc = await pool.acquire()
    acquire_ms = int((time.perf_counter() - t_acq) * 1000)
    logger.info(f"🐳 [CODE_EXEC:{exec_id}] acquired warm container {pc.id} in {acquire_ms}ms")
    if progress_cb:
        await progress_cb({
            "phase": "container", "status": "ready",
            "container": pc.id, "duration_ms": acquire_ms,
        })

    container = pc.container
    archive = await asyncio.to_thread(_build_workspace_archive, script_path, input_dir)
    try:
        await asyncio.to_thread(container.put_archive, "/workspace", archive)

        stdout, stderr, exit_code = await _exec_streaming(
            container, exec_id=exec_id, progress_cb=progress_cb,
        )

        if exit_code == 0:
            await asyncio.to_thread(_extract_output_archive, container, output_dir)

        return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}

    except Exception as e:
        logger.error(f"❌ [CODE_EXEC:{exec_id}] container error: {e}", exc_info=True)
        return {"stdout": "", "stderr": f"Execution error: {e}", "exit_code": 1}
    finally:
        # Always return container to pool — release() resets the workspace
        # and replaces the container if reset fails.
        try:
            await pc.release()
        except Exception:
            logger.exception(f"[CODE_EXEC:{exec_id}] pool release failed")


async def _exec_streaming(
    container,
    *,
    exec_id: str,
    progress_cb: ProgressCallback = None,
) -> tuple:
    """
    Run ``python /workspace/script.py`` inside ``container`` and stream
    stdout/stderr line-by-line through ``progress_cb`` (if provided).

    Returns ``(stdout, stderr, exit_code)``.
    """
    api = container.client.api
    exec_inst = await asyncio.to_thread(
        api.exec_create,
        container.id,
        cmd=["python", "/workspace/script.py"],
        user="sandbox",
        workdir="/workspace",
        stdout=True, stderr=True,
    )
    exec_inst_id = exec_inst["Id"] if isinstance(exec_inst, dict) else exec_inst

    # Streamed, demuxed output: a generator yielding (stdout_chunk, stderr_chunk).
    socket_stream = await asyncio.to_thread(
        api.exec_start, exec_inst_id, stream=True, demux=True,
    )

    out_buf = bytearray()
    err_buf = bytearray()
    pending_out = b""
    pending_err = b""

    async def _emit_lines(buf: bytes, channel: str) -> bytes:
        """Emit complete lines from ``buf``; return any unfinished tail."""
        if not progress_cb or not buf:
            return b""
        text = buf.decode("utf-8", errors="replace")
        if "\n" not in text:
            return buf
        *lines, tail = text.split("\n")
        for line in lines:
            if line:
                await progress_cb({
                    "phase": "stdout" if channel == "out" else "stderr",
                    "line": line[:1000],
                })
        return tail.encode("utf-8", errors="replace")

    # Drain the (blocking) generator in a thread, feeding chunks into an
    # asyncio.Queue so we can interleave progress_cb on the event loop.
    queue: "asyncio.Queue" = asyncio.Queue()
    sentinel = object()
    loop = asyncio.get_event_loop()

    def _drain():
        try:
            for chunk in socket_stream:
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    drain_task = loop.run_in_executor(None, _drain)

    deadline = time.perf_counter() + EXECUTION_TIMEOUT
    timed_out = False
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            timed_out = True
            break
        try:
            item = await asyncio.wait_for(queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            timed_out = True
            break
        if item is sentinel:
            break
        out_chunk, err_chunk = item if isinstance(item, tuple) else (item, None)
        if out_chunk:
            out_buf.extend(out_chunk)
            pending_out = await _emit_lines(pending_out + out_chunk, "out")
        if err_chunk:
            err_buf.extend(err_chunk)
            pending_err = await _emit_lines(pending_err + err_chunk, "err")

    # Flush any tail line
    if progress_cb:
        for tail, ch in ((pending_out, "stdout"), (pending_err, "stderr")):
            if tail:
                await progress_cb({"phase": ch, "line": tail.decode("utf-8", errors="replace")[:1000]})

    if timed_out:
        logger.warning(f"⏰ [CODE_EXEC:{exec_id}] exec timed out after {EXECUTION_TIMEOUT}s — killing")
        try:
            await asyncio.to_thread(container.kill)
        except Exception:
            pass
        try:
            drain_task.cancel()
        except Exception:
            pass
        return (
            out_buf.decode("utf-8", errors="replace"),
            err_buf.decode("utf-8", errors="replace") + f"\n[exec timed out after {EXECUTION_TIMEOUT}s]",
            124,
        )

    inspect = await asyncio.to_thread(api.exec_inspect, exec_inst_id)
    exit_code = int(inspect.get("ExitCode") or 0)
    return (
        out_buf.decode("utf-8", errors="replace"),
        err_buf.decode("utf-8", errors="replace"),
        exit_code,
    )


def build_execute_code_tool():
    """Build the execute_code function tool as an OpenAI-compatible dict.

    Shared by quick_chat and main chat streaming.
    """
    return {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "Execute a Python script to generate downloadable files. Read from /workspace/input/, write output to /workspace/output/. Available libraries: pandas, openpyxl, xlrd, python-docx, python-pptx, Pillow, xlsxwriter, reportlab, pdfplumber, jsonschema. You can generate Excel, CSV, DOCX, PPTX, PDF, and image files. Use this tool ONLY for file generation (not for charts — charts use inline chartjs code blocks). Do NOT use subprocess, os.system, network libraries, or any packages not listed here.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "Python script to execute. Read files from /workspace/input/ and write output to /workspace/output/."
                    },
                    "output_filename": {
                        "type": "string",
                        "description": "Expected output filename (e.g., 'result.json', 'output.xlsx')"
                    },
                    "input_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Exact filenames of the user's STRUCTURED vault files "
                            "(CSV/Excel/JSON) to mount for this run — get them from "
                            "list_vault_files. Each is mounted at /workspace/input/<filename>. "
                            "Omit if the script needs no vault file."
                        ),
                    }
                },
                "required": ["script", "output_filename"]
            },
        },
    }


def _guess_content_type(filename: str) -> str:
    """Guess MIME type from filename extension."""
    ext = os.path.splitext(filename)[1].lower()
    content_types = {
        '.json': 'application/json',
        '.csv': 'text/csv',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.xls': 'application/vnd.ms-excel',
        '.pdf': 'application/pdf',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.txt': 'text/plain',
        '.html': 'text/html',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.svg': 'image/svg+xml',
    }
    return content_types.get(ext, 'application/octet-stream')
