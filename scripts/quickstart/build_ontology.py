#!/usr/bin/env python3
# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Build a sources.json by asking a model to interview you about your database.

Introspection derives STRUCTURE — tables, columns, types, keys. It can never
derive MEANING: which table records decisions already made, what a document
column IS, which column is money. A person has to say. Until now that person had
to hand-edit JSON against a field reference.

This is an AGENT, not a one-shot generator. It pulls your schema itself, asks
clarifying questions, drafts, validates, and only then writes a file you confirm.

    python build_ontology.py --kind postgres --conn "postgresql://u:p@host/db"
    python build_ontology.py --kind mongo --conn "mongodb://localhost:27017/mydb"

Two rules it runs under, both deliberate:

  * YOUR CONNECTION STRING NEVER REACHES THE MODEL. This script holds it and runs
    the queries; the agent asks for `describe_tables(["claims"])` and receives
    schema. Credentials are never in a prompt, a tool argument, or a transcript.
  * THE AGENT CANNOT RUN ARBITRARY SQL. It gets fixed introspection tools. Letting
    a model write its own query would be remote code execution against your
    production database in exchange for nothing.

Nothing here is mandatory: templates and hand-authoring still work. This is the
guided path, not the only one.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP = REPO_ROOT / "source-mcp-template"
sys.path.insert(0, str(REPO_ROOT / "scripts" / "quickstart"))

DEFAULT_MODEL = os.getenv("ONTOLOGY_MODEL", "deepseek/deepseek-v4-pro")
MAX_ROUNDS = int(os.getenv("ONTOLOGY_MAX_TOOL_ROUNDS", "20"))
MAX_INPUT_TOKENS = int(os.getenv("ONTOLOGY_MAX_INPUT_TOKENS", "200000"))
MAX_OUTPUT_TOKENS = int(os.getenv("ONTOLOGY_MAX_OUTPUT_TOKENS", "200000"))

# Rough chars-per-token; only used to decide WHEN to compact, never billed on.
_CPT = 3.5


# The model writes arrows, em-dashes and box characters. A Windows console
# defaults to cp1252 and raises UnicodeEncodeError mid-run when it meets one,
# killing an otherwise-good session several rounds in. Force UTF-8 and degrade
# rather than crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a real stream (piped/captured)
        pass


def env(key: str, default: str = "") -> str:
    """Environment FIRST, then .env. An explicitly exported value must win — the
    other way round makes `KEY=x python build_ontology.py` silently do nothing,
    which is a miserable thing to debug."""
    if os.getenv(key):
        return os.environ[key]
    f = REPO_ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return default


# ── The contract the model is held to ───────────────────────────────────────
def build_system_prompt() -> str:
    """Assembled from the SHIPPED artefacts, never hand-copied — a prompt that
    restates the schema in prose drifts from the models the moment either
    changes, and the drift is invisible until a file fails to validate."""
    schema = (MCP / "schema" / "sources.schema.json").read_text(encoding="utf-8")
    grammar = (MCP / "templates" / "README.md").read_text(encoding="utf-8")
    example = (MCP / "templates" / "banking-loan_recovery-IN.sources.json").read_text(encoding="utf-8")
    return f"""You are an ontology author for Citra's `sources.json` registry.

Your job is to turn someone's database into a registry that declares not just
its STRUCTURE but its MEANING. Structure you can read with tools. Meaning you
must ASK about — you cannot infer it reliably, and a confident wrong answer is
worse than no answer, because screening then runs on the wrong document and
looks correct.

## How you work

1. `list_tables` first. Never invent a table or column name.
2. `describe_tables` in BATCHES — pass many names at once, not one per call.
3. `ask_user` whenever meaning is unclear. You have a limited number of tool
   rounds, so ask several things in one question rather than drip-feeding.
4. Draft the registry, then `validate_draft` it. Fix what it reports and
   validate again.
5. `save` only once validation passes.

## What you MUST ask about, never guess

- **decision_history** — which table records decisions already made, and which
  column IS the decision. Without it apps cannot ground in past cases.
- **artifact_role** — for every document/image column: is it `evidence`
  (fraud-relevant), `identity` (verification, duplicates are EXPECTED and must
  never be flagged), `payment_proof` (pins the receipt-vs-ledger check), or
  `supporting` (never fingerprinted)? Getting this wrong is the worst error you
  can make here.
- **value_semantics** — which column is money, and what it means. Drives ROI.
- **write-back** — ask which columns, if any, the system may UPDATE, and who
  may approve it. Everything is READ-ONLY until this is answered: a dataset with
  no `write_actions` has no write path at all, which is the safe default and
  also a system that can only ever recommend.

  Ask it in the operator's own words. They know their tables and columns. They
  do not know what an `input_schema` is and must not be asked for one:

      "Once a decision is made, should the system be able to write it back?
       If so — which table, and which columns may it update? Everything else
       stays read-only. And which role approves that write?"

  Accept table and column names. If they say no, or are unsure, write no
  `write_actions` and say plainly that the deployment is read-only and can be
  given a write action later.

  YOU build the action from that answer — this is the part they cannot write:

    * `id` / `verb`   — a name for it, e.g. `record_decision` / `update`.
    * `key_fields`    — the primary key you already read with `describe_tables`,
                        so the action can only ever address one identified row.
    * `input_schema`  — properties for EXACTLY the columns they named and no
                        others, typed from the real column types. This is the
                        boundary: a field absent here cannot be written, so
                        adding a column to it later is a deliberate act.
    * `sql_template`  — a parameterised UPDATE touching only those columns,
                        keyed on `key_fields`. Never string-built SQL, never a
                        template that could widen to other columns.
    * `roles_allowed_write` — MUST be platform roles, not job titles. The only
                        valid values are: user, IT-workflow, dept_admin,
                        org_admin, super_admin, decision-app-builder. The
                        schema types this as a plain string array, so an
                        invented role like "credit_manager" VALIDATES and then
                        matches nobody -- the action exists, passes every check
                        and can never be invoked by anyone. Map what they say
                        onto a real role and read the mapping back: "your credit
                        managers are dept_admin here -- so only they can approve
                        it." If they name no approver, ask; do not default it.
                        An action anyone may invoke is not a governed write.

  Then read it back in their words before saving — "the system may update
  status and decision_reason on loan_applications, for one application at a
  time, and only a dept_admin may approve it" — and let them correct it.
  Confirm this one out loud even when the rest was inferred: it is the only
  answer here that lets software change their records.

- **domain** — ask LAST, in one question. The industry itself is optional, but
  three of its fields change ANSWERS rather than presentation, so ask them
  plainly rather than defaulting:

    * `country` — an ISO code. Drives the locale pack.
    * `currency` — an ISO code. A money column read as the wrong currency makes
      every threshold, every ROI figure and every "flag anything over 50,000"
      wrong by whatever the exchange rate happens to be, and nothing looks
      broken.
    * `date_order` — DMY or MDY. 03/04/2026 is two different days. Get it wrong
      and every ageing, every "older than 30 days" and every date comparison is
      silently off, most visibly near month boundaries. If the schema uses real
      DATE types this matters less; if dates arrive as strings it matters a
      great deal, so look before you ask and say which you found.

  `vertical` is what you should offer a guess at, from the tables you have read
  — "this looks like lending; correct me" — because it only selects vertical
  defaults. The three above should be confirmed, not guessed. `sub_vertical`,
  `region`, `language` and `notes` are optional and not worth a round trip.

## What you may infer without asking

Types, primary keys, foreign keys, obvious descriptions, `semantic_type`, and
whether a column looks like PII. State what you inferred so the user can correct
it.

## Hard rules

- Unknown keys are a BOOT FAILURE (`extra="forbid"`). Only fields in the schema.
- Never put credentials in the file. Use `connection.env_prefix`.
- `write_actions` is opt-in, exactly like `fraud_screening`. Absent means the
  dataset is READ-ONLY — no write path exists for it, not a disabled one. Never
  add one the user did not ask for, and never widen an `input_schema` beyond the
  columns they named.
- `fraud_screening` is opt-in. Absent means "no screening", which is a valid and
  common choice — do not switch it on to look thorough.
- If the user says they do not know, leave the field out and say what that costs.

## THE SCHEMA (authoritative — every field, type and enum)

{schema}

## THE GRAMMAR (the four dataset parts and the four artifact roles)

{grammar}

## A COMPLETE WORKED EXAMPLE

{example}
"""


TOOLS = [
    {"type": "function", "function": {
        "name": "list_tables",
        "description": "List every table/collection name in the connected database. Call this first.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "describe_tables",
        "description": ("Full schema for the named tables: columns, types, primary keys, "
                        "foreign keys, enum values, ranges and sample rows. Pass MANY names "
                        "at once — each call costs a tool round."),
        "parameters": {"type": "object", "properties": {
            "names": {"type": "array", "items": {"type": "string"}}},
            "required": ["names"]}}},
    {"type": "function", "function": {
        "name": "ask_user",
        "description": ("Ask the human a clarifying question about MEANING. Combine several "
                        "related questions into one call."),
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"}}, "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "validate_draft",
        "description": ("Validate a candidate sources.json. Returns hard problems that must be "
                        "fixed, plus capability advisories describing what the file does NOT "
                        "switch on."),
        "parameters": {"type": "object", "properties": {
            "sources_json": {"type": "string"}}, "required": ["sources_json"]}}},
    {"type": "function", "function": {
        "name": "save",
        "description": "Write the final registry. Only call after validate_draft passes.",
        "parameters": {"type": "object", "properties": {
            "sources_json": {"type": "string"}}, "required": ["sources_json"]}}},
]


class Tools:
    """Every tool runs HERE. The model sees results, never the connection string."""

    def __init__(self, kind: str, conn: str, out: Path):
        self.kind, self.conn, self.out = kind, conn, out
        self._cache: Dict[str, Any] = {}
        self.saved: str | None = None
        self.validated_ok = False

    def list_tables(self) -> Any:
        import introspect_source as ins
        try:
            ds = ins.introspect(self.kind, self.conn, [])
        except SystemExit as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": self._driver_hint(e)}
        names = [d.get("physical_name") or d.get("id") for d in ds]
        for d in ds:
            self._cache[d.get("physical_name") or d.get("id")] = d
        return {"tables": names, "count": len(names)}

    def describe_tables(self, names: List[str]) -> Any:
        import introspect_source as ins
        missing = [n for n in names if n not in self._cache]
        if missing:
            try:
                for d in ins.introspect(self.kind, self.conn, missing):
                    self._cache[d.get("physical_name") or d.get("id")] = d
            except Exception as e:  # noqa: BLE001
                return {"error": self._driver_hint(e)}
        out = []
        for n in names:
            d = self._cache.get(n)
            if not d:
                out.append({"name": n, "error": "no such table — use a name from list_tables"})
                continue
            # Sample rows can carry real customer data; the model needs SHAPE,
            # not content, so send a couple and let descriptions come from names.
            d = dict(d)
            if "_sample_rows" in d:
                d["_sample_rows"] = d["_sample_rows"][:2]
            out.append(d)
        return {"tables": out}

    def ask_user(self, question: str) -> Any:
        print("\n  " + "\n  ".join(question.strip().splitlines()))
        try:
            ans = input("\n  your answer > ").strip()
        except EOFError:
            ans = ""
        return {"answer": ans or "(no answer — decide sensibly and say what you assumed)"}

    @staticmethod
    def run_validator(sources_json: str) -> tuple[bool, str]:
        """Run the REAL validator — the same one the MCP boots with — on this text.

        Shelling out to `validate_sources.py` rather than reimplementing any of
        it is the point: a second opinion that agrees with the first is worth
        nothing. If the MCP would refuse to boot on this registry, this must
        refuse to write it.
        """
        try:
            json.loads(sources_json)
        except json.JSONDecodeError as e:
            return False, f"not valid JSON: {e}"
        with tempfile.NamedTemporaryFile("w", suffix=".sources.json", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(sources_json)
            tmp = fh.name
        try:
            p = subprocess.run([sys.executable, str(MCP / "validate_sources.py"), tmp],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        finally:
            os.unlink(tmp)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")

    def validate_draft(self, sources_json: str) -> Any:
        ok, report = self.run_validator(sources_json)
        self.validated_ok = ok
        return {"valid": ok, "report": report}

    def save(self, sources_json: str) -> Any:
        """Validate the EXACT bytes being saved, not a flag set by a past call.

        `validated_ok` is sticky: the model could validate draft A, then call
        save() with draft B, and the old gate passed because it only looked at
        the flag. save() took its own argument and never re-checked it. A gate
        that can be walked around is not a gate, so the validator runs again
        here on precisely what is about to be written.
        """
        ok, report = self.run_validator(sources_json)
        self.validated_ok = ok
        if not ok:
            return {"error": "this draft does not validate — fix it and save again",
                    "report": report}
        self.saved = sources_json
        return {"saved": True, "note": "done — stop calling tools and summarise for the user"}

    @staticmethod
    def _driver_hint(exc: Exception) -> str:
        """Turn SQLAlchemy's NoSuchModuleError into the pip command that fixes it."""
        try:
            import introspect_source as ins
            for kind, pkg in getattr(ins, "_DIALECT_PKG", {}).items():
                if kind in str(exc).lower():
                    return f"{exc} — this source needs: pip install {pkg}"
        except Exception:  # noqa: BLE001
            pass
        return str(exc)


def _tokens(messages: List[Dict]) -> int:
    return int(len(json.dumps(messages, default=str)) / _CPT)


def _compact(messages: List[Dict]) -> List[Dict]:
    """Drop the BODY of the oldest tool results, keeping their identity. Losing
    early raw schema is survivable; failing at round 15 is not."""
    for m in messages:
        if m.get("role") == "tool" and len(m.get("content", "")) > 400:
            try:
                d = json.loads(m["content"])
                names = [t.get("physical_name") or t.get("name") for t in d.get("tables", [])]
                m["content"] = json.dumps({"compacted": True, "tables": names})
            except Exception:  # noqa: BLE001
                m["content"] = json.dumps({"compacted": True})
            return messages
    return messages


# Connector family -> the dataset `kind` the MCP dispatches reads on. Mirrors
# source-mcp-template/catalogue._source_kind; keep them in step.
_KIND_BY_CONN = {
    "postgres": "sql", "postgresql": "sql", "mysql": "sql", "mariadb": "sql",
    "mssql": "sql", "sqlserver": "sql", "oracle": "sql", "snowflake": "sql",
    "duckdb": "duckdb", "bigquery": "bigquery", "mongodb": "mongodb",
    "rest_api": "rest", "rest": "rest", "odata": "odata", "sap_odata": "odata",
    "salesforce": "soql", "sfdc": "soql", "soql": "soql",
}


def _fill_read_via(srcs: list) -> int:
    """Give every dataset an explicit ``kind`` and ``read_via``.

    Derived, not asked for: the target is the physical table/collection name,
    which introspection already knows exactly. Prompting a model for it invites
    it to echo the display label instead.

    Without these the registry still VALIDATES — both fields are optional in the
    schema — and the MCP boots, registers with discovery and gets crawled. Only
    an actual read fails, with a 404 naming a dataset that /datasets happily
    lists. That is a bad failure to hand someone on their first build, so the
    generated file states the mapping rather than leaning on a default.
    """
    filled = 0
    for s in srcs:
        # visibility, or the source is readable by role "user" ONLY. That is the
        # default in source-mcp-template/auth.is_visible when the key is absent,
        # and it excludes org_admin — the identity the Decision App builder runs
        # as. The result is a 404 that says the DATASET does not exist, because
        # visibility failures are deliberately non-disclosing, so it reads as a
        # missing table on a source that /health and the crawler both report as
        # fine. Hand-authored registries all set this; a generated one must too.
        if not s.get("visibility"):
            s["visibility"] = {
                "roles_allowed": ["user", "dept_admin", "org_admin", "super_admin"],
            }
            filled += 1
        if s.get("is_active") is None:
            s["is_active"] = True
            filled += 1
        conn = ((s.get("connection") or {}).get("type") or "").lower()
        kind = _KIND_BY_CONN.get(conn)
        if not kind:
            print(f"  [!] connection type {conn!r} has no known dataset kind — "
                  f"leaving read_via unset on source {s.get('source_id')!r}. "
                  f"Fill it by hand before the MCP can read from it.", file=sys.stderr)
            continue
        for d in s.get("datasets") or []:
            target = d.get("physical_name") or (d.get("id") or "").split(".")[-1]
            if not target:
                continue
            if not d.get("kind"):
                d["kind"] = kind
                filled += 1
            if not d.get("read_via"):
                d["read_via"] = {"kind": kind, "target": target}
                filled += 1
    return filled


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", required=True, help="postgres | mysql | mssql | oracle | mongo | odata | salesforce | rest | ...")
    ap.add_argument("--conn", required=True, help="connection string — never sent to the model")
    ap.add_argument("--out", default=str(REPO_ROOT / "my-source" / "sources.json"))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--org-id", help="org_id for the registry — passed in so the agent never has to ask")
    ap.add_argument("--dept", help="dept_id for the registry")
    ap.add_argument("--yes", action="store_true", help="skip the write confirmation (scripted runs)")
    args = ap.parse_args()

    key = env("LLM_API_KEY")
    base = env("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    if not key:
        print("  LLM_API_KEY not set — run the wizard first.", file=sys.stderr)
        return 2

    out = Path(args.out)
    tools = Tools(args.kind, args.conn, out)

    print(f"  model      : {args.model}")
    print(f"  database   : {args.kind}  (connection string stays local)")
    print(f"  round cap  : {args.rounds}")
    print()

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content":
            "Build the sources.json for my database. Start by discovering what is in it, "
            "then ask me whatever you need about what the tables MEAN.\n"
            + (f"\nUse org_id={args.org_id!r} and dept_id={args.dept!r} on every source. "
               "These are already decided — do not ask about them, and never write "
               "REPLACE_ placeholders."
               if (args.org_id and args.dept) else
               "\nAsk me for org_id and dept_id before saving; never write REPLACE_ "
               "placeholders, they make the catalogue unreachable.")},
    ]

    total_cost = 0.0
    blank = 0
    for rnd in range(1, args.rounds + 1):
        while _tokens(messages) > MAX_INPUT_TOKENS:
            before = _tokens(messages)
            messages = _compact(messages)
            if _tokens(messages) >= before:
                break  # nothing left to compact
            print(f"  [compacted older schema to stay under {MAX_INPUT_TOKENS:,} input tokens]")

        body = {"model": args.model, "messages": messages, "tools": TOOLS,
                "max_tokens": MAX_OUTPUT_TOKENS}
        req = urllib.request.Request(f"{base}/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=600).read())
        except urllib.error.HTTPError as e:
            print(f"  LLM call failed: HTTP {e.code} {e.read().decode()[:300]}", file=sys.stderr)
            return 1
        if resp.get("error"):
            print(f"  LLM error: {resp['error']}", file=sys.stderr)
            return 1

        usage = resp.get("usage") or {}
        total_cost += float(usage.get("cost") or 0)
        msg = resp["choices"][0]["message"]
        messages.append(msg)

        if msg.get("content"):
            print(f"  {msg['content'].strip()[:1500]}")

        calls = msg.get("tool_calls") or []
        if not calls:
            # The model asked in PROSE instead of calling ask_user. That is not
            # "finished" — treating it as finished ends a good session two turns
            # in, which is exactly what happened the first time this ran. Answer
            # it and keep going; only an actual save (or the round cap) ends the
            # loop.
            if tools.saved:
                break
            try:
                reply = input("\n  your answer > ").strip()
            except EOFError:
                reply = ""
            if not reply:
                blank += 1
                if blank >= 2:
                    print("  No further input — stopping.", file=sys.stderr)
                    break
                reply = ("Continue. Draft the registry now, call validate_draft, "
                         "fix anything it reports, then save.")
            else:
                blank = 0
            messages.append({"role": "user", "content": reply})
            continue

        for call in calls:
            name = call["function"]["name"]
            try:
                a = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                a = {}
            fn = getattr(tools, name, None)
            if fn is None:
                result: Any = {"error": f"no such tool {name}"}
            else:
                print(f"  · {name}({', '.join(f'{k}={str(v)[:40]}' for k, v in a.items() if k != 'sources_json')})")
                try:
                    result = fn(**a)
                except TypeError as e:
                    result = {"error": f"bad arguments: {e}"}
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, default=str)[:60000]})

        if tools.saved:
            break
    else:
        print(f"\n  Reached the {args.rounds}-round cap. Saving the best draft so far "
              f"rather than losing the work.", file=sys.stderr)

    print(f"\n  cost this run: ${total_cost:.4f}")

    if not tools.saved:
        print("  The agent did not produce a validated registry. Nothing written.", file=sys.stderr)
        return 1

    # -- the human confirms before anything lands ----------------------------
    doc = json.loads(tools.saved)
    srcs = doc if isinstance(doc, list) else (doc.get("sources") or [])
    print("\n  Proposed registry:")
    for s in srcs:
        print(f"    source {s.get('source_id')}  org={s.get('org_id')} dept={s.get('dept_id')}")
        for d in s.get("datasets") or []:
            roles = [c.get("artifact_role") for c in (d.get("columns") or []) if c.get("artifact_role")]
            bits = []
            if d.get("decision_history"):
                bits.append("decision history")
            if d.get("value_semantics"):
                bits.append("value")
            if roles:
                bits.append("artifacts: " + ", ".join(sorted(set(roles))))
            print(f"      - {d.get('id')}  {'| ' + '; '.join(bits) if bits else ''}")

    if args.yes:
        ok = True
    else:
        try:
            ok = input(f"\n  Write this to {out}? (y/n) ").strip().lower() in ("y", "yes")
        except EOFError:
            ok = False
    if not ok:
        print("  Not written.")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    n_filled = _fill_read_via(srcs)
    if n_filled:
        print(f"  [ok] derived kind/read_via for {n_filled} dataset field(s)")
    payload = doc if isinstance(doc, dict) else srcs
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + chr(10), encoding="utf-8")
    print(f"  wrote {out}")

    # Validate what LANDED, not what we intended to write. Everything before
    # this validated a string in memory; this is the only check that covers the
    # write itself -- encoding, truncation, a half-written file on a full disk.
    # It used to print `next: validate_sources.py ...` and leave it to the
    # human, which makes verification optional in practice.
    #
    # Exits non-zero on failure BUT leaves the file in place: you need to read
    # it to see what went wrong, and a registry the MCP will refuse to boot on
    # is more useful than no registry at all -- as long as nobody is told it
    # passed.
    ok, report = Tools.run_validator(out.read_text(encoding="utf-8"))
    if not ok:
        print("\n  [FAIL] the file on disk does NOT validate:", file=sys.stderr)
        print(report.rstrip(), file=sys.stderr)
        print(f"\n  It was left at {out} so you can inspect it. Do NOT boot an "
              f"MCP against it — the registry is strict and would fail at "
              f"startup.", file=sys.stderr)
        return 1
    print(f"  [ok] validates against {MCP.name}/validate_sources.py")

    # An interview cannot cover a 28-field source, an 18-field dataset and a
    # 17-field column without becoming an interrogation nobody finishes. It asks
    # the handful that change answers and leaves the rest at defaults -- so say
    # so, name the file, and say how to change it. Anything not said here gets
    # discovered by reading the source code, which is a bad way to learn that
    # your deployment has a feature.
    print()
    print("  ── what was NOT asked ──────────────────────────────────────────")
    print("  Sensible defaults were taken for everything else. Each one below")
    print("  names the section of the guide that documents it:")
    print("    fraud screening    OFF    document reuse / tampering      s10, s10.2")
    print("    artifact reuse     OFF    per-column reuse_policy         s10.1")
    print("    payment proof      off    receipt-vs-ledger matching      s10.2")
    print("    verify_against     off    document-vs-record comparison   s10.3")
    print("    date_rules         none   declarative date sanity rules   s10.4")
    print("    mandatory checks   none   bureau / KYC / sanctions gates  s11")
    print("    visibility         default roles                          s3")
    print("    organization       none   customer display identity       s2.2")
    print("    taxonomy           none   doc types, classification       s2")
    print("    per-column sensitivity                                    s6")
    print()
    print("  SOPs are NOT in that list, because they are not declared here at")
    print("  all. A department's SOP/policy library is created by UPLOADING")
    print("  documents -- this wizard's SOP step, or Home -> SOP Library -> New")
    print("  library -> Upload SOPs -- and it registers itself. Adding a")
    print("  semantic source to this file as well would create a SECOND,")
    print("  empty library that cannot see the documents you uploaded.")
    print()
    print("  NONE of that is hidden or paid. It is declarative, and it is all")
    print("  in one reviewable file with no secrets in it (sources declare")
    print("  env_prefix, never a password):")
    print(f"    {out}")
    print()
    print("  READ THIS to see everything the ontology can express -- every")
    print("  field, with worked examples, in about 1,100 lines:")
    print(f"    {MCP.name}/docs/")
    print(f"    {MCP.name}/docs/sources-file.md")
    print()
    print("  After editing, validate and restart the two services that read it:")
    print(f"    python {MCP.name}/validate_sources.py {out}")
    print("    docker compose restart citra-mcp-service     # registry is cached at boot")
    print("    docker compose restart data-discovery-service # catalogue crawls at startup")
    print()
    print("  Both load their view ONCE on startup, so an edit without a restart")
    print("  changes nothing and looks like the edit did not work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
