#!/usr/bin/env python3
# Copyright (c) 2024-2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# PROPRIETARY - all rights reserved. See LICENSE.md. NOT an open-source grant.
# SPDX-License-Identifier: LicenseRef-Citra-AI-Proprietary
"""
Generate (and optionally start) a CUSTOM Citra MCP for an org's registered sources.

`introspect_source.py` writes sources.json entries; this turns
those entries into a runnable MCP container — the missing piece between "source
registered" and "a custom MCP serving it + registered with discovery-service".

It reads a sources.json registry for an org (+ optional depts), and emits
`deployments/<org>/mcp/docker-compose.yml` that **builds from `source-mcp-template`**
(so every connector + NL→query planner ships in the image — no per-tenant code copy),
wiring ORG_ID / DEPT_IDS / SOURCES_FILE / DISCOVERY_URL / MCP_API_KEY / JWT_SECRET
from the root `.env`, plus a **connection-env block per source** (the exact var names
each connector reads) for you to fill with real credentials. On boot the MCP loads its
sources and self-registers with discovery-service.

  python scripts/make_mcp.py --org acme --depts billing,vigilance        # generate
  python scripts/make_mcp.py --org acme --up                             # generate + start

`--up` requires Docker + the main stack already running.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.parse as _url
from typing import Any, Dict, List, Tuple


# ── connection-env matrix: the EXACT vars each connector reads (per env_prefix) ──
def _env_block(prefix: str, kind: str) -> List[Tuple[str, str, str]]:
    """Return [(VAR, default, comment)] the MCP's connector reads for this source kind."""
    p = prefix.upper()
    if kind == "mongodb":
        return [(f"{p}_URI", "", "full pymongo URI, e.g. mongodb://user:pass@host:27017"),
                (f"{p}_DB", "", "database name")]
    if kind == "odata":
        return [(f"{p}_BASE_URL", "", "https://host[:port]/path/svc"),
                (f"{p}_USER", "", "basic-auth user (or use _TOKEN_URL/_CLIENT_ID/_CLIENT_SECRET for oauth)"),
                (f"{p}_PASS", "", "basic-auth password")]
    if kind == "soql":
        return [(f"{p}_INSTANCE_URL", "", "https://company.my.salesforce.com"),
                (f"{p}_CLIENT_ID", "", "Connected App consumer key"),
                (f"{p}_CLIENT_SECRET", "", "Connected App consumer secret"),
                (f"{p}_REFRESH_TOKEN", "", "refresh-token auth flow")]
    if kind == "rest":
        return [(f"{p}_TOKEN", "", "bearer token (or {0}_API_KEY, or {0}_USER + {0}_PASSWORD)".format(p))]
    # sql / warehouses / bigquery — SQLAlchemy connector
    return [(f"{p}_HOST", "", ""), (f"{p}_PORT", "5432", ""), (f"{p}_DB", "", ""),
            (f"{p}_USER", "", ""), (f"{p}_PASS", "", "")]


def _kind_of(doc: Dict[str, Any]) -> str:
    """Mirror source-mcp-template/catalogue._source_kind dispatch."""
    t = str(doc.get("type", "")).lower()
    sub = str((doc.get("connection") or {}).get("type", "")).lower()
    if t == "mongodb":
        return "mongodb"
    if t == "rest_api":
        return "rest"
    if t == "bigquery":
        return "bigquery"
    if sub in ("odata", "sap_odata"):
        return "odata"
    if sub in ("salesforce", "sfdc", "soql"):
        return "soql"
    return "sql"


def _envfile_get(path: str, key: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and line.split("=", 1)[0] == key:
                    return line.split("=", 1)[1]
    except FileNotFoundError:
        pass
    return default


def _free_port(start: int = 8510) -> int:
    for p in range(start, start + 300):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def _docker_network() -> str:
    """The docker network a generated MCP joins — from the root .env.

    NOT hardcoded, and deliberately without a default. This repo runs on
    citra-ai-net and the public citra-decision-system quickstart runs on
    citra-network, so any literal here is wrong in one of the two trees. Baking
    one in meant a fix made against one repo's compose silently broke the
    other's, which is exactly what happened on 2026-08-17. Keeping the code
    identical and moving the deployment value into .env is what makes the two
    trees mirror-able at all.

    Fails loud rather than guessing: a wrong network produces
    `network X declared as external, but could not be found` at `up` time,
    far from the cause.
    """
    net = (os.getenv("CITRA_DOCKER_NETWORK") or "").strip()
    if not net:
        raise SystemExit(
            "CITRA_DOCKER_NETWORK is not set.\n"
            "  It names the docker network the generated MCP joins, and it differs\n"
            "  per deployment, so there is no safe default. Set it in the root .env\n"
            "  to the network your stack actually runs on — check with:\n"
            "      docker network ls | grep citra")
    return net


def _render(org: str, depts: List[str], port: int,
            prefixes: Dict[str, Tuple[str, str]]) -> str:
    safe = org.lower().replace("_", "-")
    network = _docker_network()
    conn_lines: List[str] = []
    for pfx, (kind, src) in sorted(prefixes.items()):
        conn_lines.append(f"      # source '{src}' ({kind}) — fill in real credentials:")
        for var, default, comment in _env_block(pfx, kind):
            c = f"   # {comment}" if comment else ""
            conn_lines.append(f'      {var}: "{default}"{c}')
    conn_block = "\n".join(conn_lines) or "      # (no env_prefix sources found)"
    dept_csv = ",".join(depts)
    return f"""# Custom MCP for org '{org}' — generated by scripts/make_mcp.py.
# Builds from source-mcp-template (every connector + NL->query planner is in the image),
# reads its source registry from the LOCAL sources.json mounted read-only at
# /app/sources.json (SOURCES_FILE), and self-registers with discovery-service on
# boot. Fill the connection-env placeholders below, then:
#   docker compose --env-file .env -f deployments/{org}/mcp/docker-compose.yml up -d --build
#   curl http://localhost:{port}/health
name: {safe}-mcp

services:
  mcp-{safe}:
    build:
      context: ../../../            # repo root (Dockerfile COPYs source-mcp-template)
      dockerfile: source-mcp-template/Dockerfile
    image: citra-mcp-{safe}
    container_name: mcp-{safe}
    restart: unless-stopped
    env_file:
      - ../../../.env               # shared LLM / embedding / JWT / MCP-key / bucket secrets
    ports:
      - "{port}:8090"
    volumes:
      # THE source registry. The MCP is file-defined: it refuses to boot without
      # SOURCES_FILE (or SOURCES_JSON). Read-only on purpose - the container must
      # never edit its own registry.
      - ./sources.json:/app/sources.json:ro
    environment:
      # ── Identity (MUST match sources.json org_id / dept_id) ──────────
      ORG_ID: {org}
      DEPT_IDS: "{dept_csv}"
      ENVIRONMENT: demo
      PORT: "8090"
      PYTHONPATH: "/app/source-mcp-template"

      # ── Source registry: the mounted file, NOT the platform Mongo ────
      # The central-Mongo `dept_sources` load mode was REMOVED from
      # source-mcp-template/config.py on 2026-07-10. Wiring CITRA_MONGO_URI here
      # would produce a container that will not start.
      SOURCES_FILE: /app/sources.json

      # ── Discovery registration (so Citra can find this MCP) ──────────
      DISCOVERY_URL: ${{DISCOVERY_URL:-http://discovery-service:9000}}
      MCP_API_KEY: ${{MCP_API_KEY:-demo-mcp-key-local-only}}
      MCP_PUBLIC_BASE_URL: http://mcp-{safe}:8090

      # ── LLM for NL->query planning (OpenRouter) ──────────────────────
      LLM_BASE_URL: ${{LLM_BASE_URL:-https://openrouter.ai/api/v1}}
      LLM_MODEL: ${{LLM_MODEL:-deepseek/deepseek-chat-v3.1}}
      LLM_API_KEY: ${{LLM_API_KEY:-}}
      PLANNER_ENABLED: ${{PLANNER_ENABLED:-true}}
      LLM_EXTRA_BODY: '{{"reasoning":{{"exclude":true}}}}'

      # ── Embeddings + Milvus (only for RAG / semantic sources) ────────
      MILVUS_URI: ${{MILVUS_URI:-http://citra-milvus:19530}}
      MILVUS_TOKEN: ${{MILVUS_TOKEN:-}}
      EMBEDDING_BASE_URL: ${{EMBEDDING_BASE_URL:-https://openrouter.ai/api/v1}}
      EMBEDDING_MODEL: ${{EMBEDDING_MODEL:-baai/bge-m3}}
      EMBEDDING_API_KEY: ${{EMBEDDING_API_KEY:-${{LLM_API_KEY:-}}}}
      EMBEDDING_DIMENSION: ${{EMBEDDING_DIMENSION:-768}}

      # ── AuthZ — verify forwarded user JWT with Citra's HS256 key ─────
      AUTHZ_ENFORCE: ${{AUTHZ_ENFORCE:-true}}
      JWT_SECRET: ${{JWT_SECRET:-}}

      # ── Object storage (signed document hand-offs) ───────────────────
      BUCKET_NAME: ${{BUCKET_NAME:-citra-documents}}
      BUCKET_ENDPOINT_URL: ${{BUCKET_ENDPOINT_URL:-http://citra-minio:9002}}
      BUCKET_ACCESS_KEY: ${{BUCKET_ACCESS_KEY:-minioadmin}}
      BUCKET_SECRET_KEY: ${{BUCKET_SECRET_KEY:-minioadmin}}

      # ── Source connection credentials (FILL THESE IN) ────────────────
{conn_block}
    healthcheck:
      test: ["CMD-SHELL", "python -c \\"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8090/health',timeout=4).status==200 else 1)\\""]
      interval: 15s
      timeout: 6s
      retries: 5
      start_period: 20s
    networks:
      - {network}
    extra_hosts:
      - "host.docker.internal:host-gateway"

networks:
  {network}:
    external: true
    name: {network}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate (and optionally start) a custom MCP from a sources.json registry.")
    ap.add_argument("--org", required=True)
    ap.add_argument("--depts", default="", help="comma-separated; blank = every dept present in the registry")
    ap.add_argument("--sources", default="",
                    help="path to sources.json (default: <out dir>/sources.json, else "
                         "demo-data/tenants/<org>/mcp/sources.json)")
    ap.add_argument("--port", type=int, default=0, help="host port (default: first free from 8510)")
    ap.add_argument("--out", default="", help="compose path (default: deployments/<org>/mcp/docker-compose.yml)")
    ap.add_argument("--up", action="store_true", help="docker compose up -d --build after generating")
    a = ap.parse_args()

    # Read the registry FILE. The Mongo `dept_sources` collection stopped being
    # the MCP's registry on 2026-07-10; reading it here would generate a compose
    # for sources the container will never load.
    src_path = a.sources
    if not src_path:
        guess = os.path.join("demo-data", "tenants", a.org, "mcp", "sources.json")
        src_path = guess if os.path.exists(guess) else ""
    if not src_path or not os.path.exists(src_path):
        print(f"[FAIL] no sources.json found for org='{a.org}'.")
        print("       Pass --sources <path>, or create one with:")
        print(f"         python scripts/quickstart/introspect_source.py --org {a.org} ... --out <path>")
        print("       See docs/change-the-demo.md.")
        return 2

    try:
        with open(src_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] could not read {src_path}: {exc}")
        return 2
    docs = loaded.get("sources", []) if isinstance(loaded, dict) else loaded
    if not isinstance(docs, list) or not docs:
        print(f"[FAIL] {src_path} contains no sources.")
        return 2

    depts_filter = [d.strip() for d in a.depts.split(",") if d.strip()]
    docs = [d for d in docs
            if d.get("org_id") == a.org
            and (not depts_filter or d.get("dept_id") in depts_filter)]
    if not docs:
        print(f"[FAIL] {src_path} has no entries for org='{a.org}'"
              + (f" depts={depts_filter}" if depts_filter else "") + ".")
        return 2

    all_depts = sorted({d.get("dept_id") for d in docs if d.get("dept_id")})
    prefixes: Dict[str, Tuple[str, str]] = {}
    for d in docs:
        pfx = ((d.get("connection") or {}).get("env_prefix") or "").strip()
        if pfx and pfx not in prefixes:
            prefixes[pfx] = (_kind_of(d), d.get("source_id", "?"))

    port = a.port or _free_port()
    out = a.out or os.path.join("deployments", a.org, "mcp", "docker-compose.yml")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(_render(a.org, all_depts, port, prefixes))

    print(f"[ok] wrote {out}")
    print(f"     org={a.org}  depts={','.join(all_depts)}  sources={len(docs)}  port={port}")
    print(f"     registry: {src_path}  ->  mounted read-only at /app/sources.json")
    print(f"     connection env to fill: {', '.join(sorted(prefixes)) or '(none)'}")
    print("     -> fill the *_HOST/_USER/_PASS (etc.) placeholders with real credentials, then:")
    print(f"       docker compose --env-file .env -f {out} up -d --build")

    if a.up:
        print("[..] docker compose up -d --build")
        rc = subprocess.run(["docker", "compose", "--env-file", ".env", "-f", out, "up", "-d", "--build"]).returncode
        if rc != 0:
            print("[FAIL] docker compose up failed")
            return rc
        print(f"[ok] MCP starting on :{port} - check  curl http://localhost:{port}/health")
        print("     it self-registers with discovery-service on boot (see its logs for 'source(s) registered').")
    return 0


if __name__ == "__main__":
    sys.exit(main())
