"""E2E: does clause memory work for EVERY modality, not just `record`?

The existing e2e_clause_memory covers `record` only, and the live data has only
`record` and `image` corrections — the `api` bucket (which is what a
check_evaluate factor writes, including a scorecard override) and `document`
have never been exercised end to end.

The four are NOT the same shape, on purpose:

  record            case facets only
  api / case        case facets + an `item_subject:<subject>` facet — these are
                    the modalities whose subject is known BEFORE the prompt (the
                    tool call names the check), so retrieval can scope on it
  image / document  case facets only. The subject is not knowable until the
                    model has looked, so the record's context is the only scope
                    they can honestly route on — item_subject_facet returns []
                    for them by design (SUBJECT_SCOPED_MODALITIES).

Runs against a LIVE service on a SCRATCH app slug, so no real tenant data is
touched. Cleans up after itself.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import jwt
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Run from anywhere: the service modules live one level up from tests/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.getenv("E2E_BASE", "http://127.0.0.1:9100")
APP = "zz-modality-probe"          # scratch: never a real app
TENANT = "acme-power"

load_dotenv(".env")
DB_URI, DB_NAME = os.environ["MONGO_URI"], os.environ.get("MONGO_DB", "dev")

CASE = ["category:theft_report", "priority:high"]

# (modality, task_type, subject) — subject only means something for api/case.
BUCKETS = [
    ("record", "decision", None),
    ("api", "credit_bureau_check", "credit_bureau_check"),
    ("image", "meter_photo", "tamper_seal"),
    ("document", "bank_statement", "statement_page"),
]

_passed, _failed = [], []


def check(name, cond, detail=""):
    (_passed if cond else _failed).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    return cond


def token():
    return jwt.encode(
        {"sub": "e2e", "user_id": "e2e", "email": "e2e@acme-power.citra.ai",
         "roles": ["super_admin"], "tenant_id": TENANT, "org_id": TENANT,
         "exp": int(time.time()) + 1800},
        os.environ["JWT_SECRET"], algorithm=os.getenv("JWT_ALGORITHM", "HS256"))


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        f"{BASE}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token()}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode(errors="replace")[:400]}


async def wipe():
    c = AsyncIOMotorClient(DB_URI)
    db = c[DB_NAME]
    await db["smartapp_corrections"].delete_many({"app_slug": APP})
    await db["smartapp_clauses"].delete_many({"app_slug": APP})
    c.close()


async def seed_all():
    """Three officers agreeing, in every bucket at once — consolidation must
    keep the buckets SEPARATE and author one clause per bucket."""
    from learned_memory import item_subject_facet

    c = AsyncIOMotorClient(DB_URI)
    db = c[DB_NAME]
    now = datetime.now(timezone.utc)
    docs = []
    for mod, tt, subject in BUCKETS:
        facets = list(CASE) + item_subject_facet(subject, mod)
        for i, officer in enumerate(("asha@x", "bhavna@x", "chetan@x")):
            docs.append({
                "correction_id": f"corr-mod-{mod}-{i}", "tenant_id": TENANT,
                "app_slug": APP, "modality": mod, "task_type": tt,
                "officer": officer, "event": "override",
                "reason_code": "wrong_department",
                "reason_text": ("theft reports must go to revenue protection, "
                                "not the local line crew"),
                "case_facets": facets,
                "contested_fields": ["assigned_to"],
                "overrides": [{"override": {"assigned_to": {
                    "from": "Line Crew", "to": "Revenue Protection"}}}],
                "injected_clause_ids": [], "cited_clause_ids": [],
                "overruled_clause_ids": [], "consumed_by": None, "at": now,
            })
    await db["smartapp_corrections"].insert_many(docs)
    c.close()
    return docs


async def clauses():
    c = AsyncIOMotorClient(DB_URI)
    rows = await c[DB_NAME]["smartapp_clauses"].find(
        {"app_slug": APP}, {"_id": 0}).to_list(200)
    c.close()
    return rows


async def main():
    from learned_memory import item_subject_facet

    await wipe()
    print("\n=== 0. the subject-facet contract ===")
    check("api IS subject-scoped",
          item_subject_facet("credit_bureau_check", "api") == ["item_subject:credit_bureau_check"],
          str(item_subject_facet("credit_bureau_check", "api")))
    check("case IS subject-scoped",
          item_subject_facet("x", "case") == ["item_subject:x"])
    check("image is NOT subject-scoped (subject unknown until the model looks)",
          item_subject_facet("tamper_seal", "image") == [],
          str(item_subject_facet("tamper_seal", "image")))
    check("document is NOT subject-scoped",
          item_subject_facet("statement_page", "document") == [])
    check("a missing subject never guesses",
          item_subject_facet(None, "api") == [] and item_subject_facet("  ", "api") == [])

    print("\n=== 1. consolidation authors one rule PER BUCKET ===")
    seeded = await seed_all()
    print(f"        seeded {len(seeded)} corrections across {len(BUCKETS)} buckets")
    st, res = api("/admin/consolidation/run", method="POST")
    r = (res or {}).get("result", {})
    check("consolidation pass succeeds", st == 200 and r.get("errors") == 0,
          f"HTTP {st} errors={r.get('errors')}")

    cl = await clauses()
    by_bucket = {(c["modality"], c["task_type"]): c for c in cl}
    check("every modality produced a rule",
          len(by_bucket) == len(BUCKETS),
          f"{sorted(by_bucket.keys())}")
    for mod, tt, _ in BUCKETS:
        c = by_bucket.get((mod, tt))
        check(f"  [{mod}] rule is ACTIVE on 3 officers",
              bool(c) and c["status"] == "active" and c["support_count"] == 3,
              (f"{c['status']}/{c['support_count']}" if c else "MISSING"))

    print("\n=== 2. buckets do not bleed into each other ===")
    api_c = by_bucket.get(("api", "credit_bureau_check"))
    img_c = by_bucket.get(("image", "meter_photo"))
    check("the api rule carries the item_subject scope",
          bool(api_c) and "item_subject:credit_bureau_check" in (api_c["scope_facets"] or []),
          str(api_c["scope_facets"]) if api_c else "-")
    check("the image rule carries NO item_subject scope",
          bool(img_c) and not any(f.startswith("item_subject:")
                                  for f in (img_c["scope_facets"] or [])),
          str(img_c["scope_facets"]) if img_c else "-")

    print("\n=== 3. RETRIEVAL is per-bucket ===")
    sys.path.insert(0, os.getcwd())
    import main as _svc
    from clause_store import select_clauses

    # Retrieval is called IN-PROCESS (no HTTP endpoint exposes it), so the
    # lazy collection accessor in main needs a handle — same bootstrap as
    # e2e_clause_memory.
    if _svc._db is None:
        _svc._db = AsyncIOMotorClient(DB_URI)[DB_NAME]

    for mod, tt, subject in BUCKETS:
        facets = list(CASE) + item_subject_facet(subject, mod)
        blk, ids = await select_clauses(
            tenant_id=TENANT, app_slug=APP, modality=mod, task_type=tt,
            case_facets=facets, budget_words=1000)
        want = by_bucket.get((mod, tt), {}).get("clause_id")
        check(f"  [{mod}] fires on its own case", ids == [want], f"{ids} want {[want]}")

    # The sharpest cross-bucket check: the api rule must NOT fire for a
    # DIFFERENT check on the same case. Same case facets, different subject.
    blk, ids = await select_clauses(
        tenant_id=TENANT, app_slug=APP, modality="api", task_type="credit_bureau_check",
        case_facets=list(CASE) + item_subject_facet("income_verification", "api"),
        budget_words=1000)
    check("the api rule does NOT fire for a different check on the same case",
          ids == [], str(ids))

    # And nothing fires on an unrelated case.
    blk, ids = await select_clauses(
        tenant_id=TENANT, app_slug=APP, modality="record", task_type="decision",
        case_facets=["category:billing_query", "priority:low"], budget_words=1000)
    check("nothing fires on an unrelated case", ids == [], str(ids))

    await wipe()
    print("\n" + "=" * 58)
    print(f"  {len(_passed)} passed, {len(_failed)} failed")
    for f in _failed:
        print(f"    FAILED: {f}")
    return 1 if _failed else 0


if __name__ == "__main__":
    import asyncio
    raise SystemExit(asyncio.run(main()))
