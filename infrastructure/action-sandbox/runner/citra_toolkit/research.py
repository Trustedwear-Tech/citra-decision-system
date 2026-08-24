# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Research audit + deterministic citation builder.

Purpose: every Action Chat response from the Deep Research Analyst MUST
end with a ``## Sources`` and an ``## Audit Trail`` section. This module
records every tool call the agent makes, then ``research.compile()``
builds those two sections deterministically — the LLM only writes the
answer body.

Usage::

    from citra_toolkit import research
    research.start("Forecast Middle East war impact on supply chain")
    research.step("plan", tool="llm.small", params={"q": "..."}, summary="3 sub-questions")
    # ... web/search/embed/rerank/llm calls (most call research.auto_step())
    body = "...analyst writes the answer here with [^1] [^2] markers..."
    out = research.compile(title="Geopolitical Risk Brief", body=body)
    print(out["markdown"])
    # → body + "\n\n## Sources\n[^1] WEB::https://...\n...\n## Audit Trail\n1. ..."
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------- state
@dataclass
class _Session:
    job_id: str
    question: str
    started_at: float
    audit: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)  # de-duplicated
    audit_path: Path | None = None


_active: _Session | None = None


def _root() -> Path:
    base = Path(os.getenv("CITRA_RESEARCH_DIR") or "/workspace/.citra-research")
    base.mkdir(parents=True, exist_ok=True)
    return base


# ----------------------------------------------------------------------- API
def start(question: str, *, job_id: str | None = None) -> str:
    """Open a new research session. Returns the session id."""
    global _active
    sid = job_id or os.getenv("CITRA_JOB_ID") or os.getenv("CITRA_SESSION_ID") or f"r-{int(time.time())}"
    out_dir = _root() / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    _active = _Session(
        job_id=sid,
        question=question,
        started_at=time.time(),
        audit_path=out_dir / "audit.jsonl",
    )
    _active.audit_path.write_text("")  # truncate
    step("start", tool="research.start", params={"question": question}, summary="audit opened")
    return sid


def _ensure_active() -> _Session:
    global _active
    if _active is None:
        # Auto-start a session keyed by job/session id so toolkit modules
        # can call ``auto_step()`` without the agent thinking about it.
        start(os.getenv("CITRA_DEEP_RESEARCH_QUESTION") or "(unspecified)")
    assert _active is not None
    return _active


def step(action: str, *, tool: str, params: dict[str, Any] | None = None,
         summary: str = "", citations: list[str] | None = None,
         result: Any = None) -> None:
    """Record an audit step and any new citations.

    ``citations`` is a list of citation tokens — typically URLs (becomes
    ``WEB::<url>``), document ids (``DOC::...``), or chunk ids (``CHUNK::...``).
    """
    sess = _ensure_active()
    entry = {
        "n": len(sess.audit) + 1,
        "ts": time.time(),
        "action": action,
        "tool": tool,
        "params": _safe(params or {}),
        "summary": summary,
    }
    if result is not None:
        entry["result"] = _safe(result, max_chars=400)
    sess.audit.append(entry)
    if sess.audit_path:
        try:
            with sess.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass
    if citations:
        for c in citations:
            _record_citation(c, used_for=summary)


def auto_step(tool: str, params: dict[str, Any] | None = None, *,
              summary: str = "", citations: list[str] | None = None) -> None:
    """Convenience used by toolkit modules to record their own calls.
    Never raises — failure to record must not break the calling tool."""
    try:
        step(action=tool.split(".", 1)[0], tool=tool, params=params,
             summary=summary, citations=citations)
    except Exception:  # noqa: BLE001
        pass


def cite_doc(document_id: str, display_name: str = "", *, used_for: str = "") -> str:
    """Register a vault document citation; return the citation token."""
    token = f"DOC::{display_name or document_id}--{document_id}"
    _record_citation(token, used_for=used_for, display=display_name or document_id)
    return token


def cite_chunk(vector_id: str, display_name: str = "", *, used_for: str = "") -> str:
    token = f"CHUNK::{display_name or vector_id}--{vector_id}"
    _record_citation(token, used_for=used_for, display=display_name or vector_id)
    return token


def cite_web(url: str, *, used_for: str = "", display: str = "") -> str:
    token = f"WEB::{url}"
    _record_citation(token, used_for=used_for, display=display or url)
    return token


def cite_mcp(dept: str, record_id: str, *, used_for: str = "") -> str:
    token = f"MCP::{dept}--{record_id}"
    _record_citation(token, used_for=used_for, display=f"{dept}/{record_id}")
    return token


def _record_citation(token: str, *, used_for: str = "", display: str = "") -> None:
    sess = _ensure_active()
    for c in sess.citations:
        if c["token"] == token:
            if used_for and used_for not in c["used_for"]:
                c["used_for"].append(used_for)
            return
    sess.citations.append({
        "n": len(sess.citations) + 1,
        "token": token,
        "display": display or token,
        "used_for": [used_for] if used_for else [],
    })


def compile(title: str = "Deep Research Report", *, body: str = "") -> dict[str, Any]:
    """Produce the deterministic ``## Sources`` + ``## Audit Trail`` blocks
    and return ``{markdown, sources, audit, job_id, question}``.

    ``body`` is the analyst's prose (already containing ``[^n]`` markers).
    The two sections are appended.
    """
    sess = _ensure_active()
    sources_lines = ["## Sources"]
    if sess.citations:
        for c in sess.citations:
            used = "; ".join(c["used_for"]) if c["used_for"] else "referenced"
            sources_lines.append(f"[^{c['n']}] {c['token']} — used for: {used}")
    else:
        sources_lines.append("_No external sources were used._")

    audit_lines = ["## Audit Trail"]
    for a in sess.audit:
        params_summary = _short(a.get("params") or {})
        audit_lines.append(
            f"{a['n']}. **{a['tool']}**({params_summary}) → {a.get('summary') or 'ok'}"
        )

    full = (body.rstrip() + "\n\n") if body else ""
    full += f"# {title}\n\n" if not body.lstrip().startswith("#") and not body else ""
    full += "\n".join(sources_lines) + "\n\n" + "\n".join(audit_lines) + "\n"

    return {
        "markdown": full,
        "sources": list(sess.citations),
        "audit": list(sess.audit),
        "job_id": sess.job_id,
        "question": sess.question,
    }


# ----------------------------------------------------------------- helpers
def _safe(obj: Any, *, max_chars: int = 200) -> Any:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        s = str(obj)
    if len(s) > max_chars:
        s = s[: max_chars - 3] + "..."
    return s


def _short(d: dict[str, Any]) -> str:
    if not isinstance(d, dict):
        return str(d)
    parts: list[str] = []
    for k, v in list(d.items())[:4]:
        sv = str(v)
        if len(sv) > 40:
            sv = sv[:37] + "..."
        parts.append(f"{k}={sv}")
    return ", ".join(parts)
