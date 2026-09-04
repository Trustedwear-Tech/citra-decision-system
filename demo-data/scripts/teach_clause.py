# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Teach the demo a judgement, the way officers actually teach one.

The acme-bank seed ships four apps, a policy library and 211,615 rows -- and no
learned judgement. `smartapp_clauses` is empty, so the one thing that separates
this from a grounded copilot is the one thing the demo cannot show.

Clauses are deliberately NOT seeded. They are FORMED:

    decision -> officer disagrees -> correction -> consolidation -> clause

Writing a clause straight into the collection would fake the provenance, which
is the part worth showing. So this runs the real loop: real cases, real runs,
real corrections from three real officer identities, then the real
consolidation pass. It is slow (each run calls the model) and that is the
point -- what comes out has a provenance trail you can open.

    docker compose exec -T smart-app-service \\
      python /app/demo-data/scripts/teach_clause.py

Why three officers: clause_store promotes on `promotion_min_officers = 3`
DISTINCT officers. Three corrections from ONE officer produce a `candidate`,
not an `active` clause -- and a screenshot of a candidate proves less than
nothing, because it looks like the feature half-worked.

Why these cases: the clause's scope is the INTERSECTION of the case facets on
the corrections that formed it. These five differ in product, ticket size, FOIR
band and income proof, and agree on exactly one thing -- they came through a
DSA. So the clause comes out scoped to `sourcing_channel:dsa` without anyone
choosing that, which is the honest version of the claim.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

# Errors have to be findable in a wall of INFO. A failed catalogue crawl printed
# one actionable line and it scrolled past unread among two thousand others; the
# run then produced four confusing 422s that read like a different problem.
# Colour only when stderr is a terminal and NO_COLOR is unset, so a piped
# transcript stays plain text.
class _LevelColour(logging.Formatter):
    _C = {"ERROR": "\033[31m", "CRITICAL": "\033[31m", "WARNING": "\033[33m"}

    def format(self, record):
        line = super().format(record)
        colour = self._C.get(record.levelname)
        return f"{colour}{line}\033[0m" if colour else line


_FMT = "%(asctime)s %(levelname)s %(message)s"
_want_colour = sys.stderr.isatty() and not __import__("os").environ.get("NO_COLOR")
_handler = logging.StreamHandler()
_handler.setFormatter((_LevelColour if _want_colour else logging.Formatter)(_FMT))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger(__name__)

SLUG = "loan-application-triage"
ORG = "acme-bank"
BASE = os.getenv("SMART_APP_SERVICE_URL", "http://localhost:9100")

# Officers who each independently reach the same conclusion. Three DISTINCT
# identities is the promotion threshold; the wording differs per officer
# because real corrections are not copy-paste, and the consolidation reads the
# text.
# Officers who each independently reach the same conclusion. THREE distinct
# identities is the promotion threshold in clause_store.
#
# These are minted JWT subjects, not registered users -- the promotion gate
# counts distinct officer identities on the corrections, and nothing here
# checks them against the user store. That is fine for seeding a demo and it
# is NOT how a real deployment works: there, three people signed in and
# disagreed with the app. Do not read the resulting clause as evidence that
# three humans were consulted; read it as a faithful reproduction of what
# happens when they are.
#
# Two gates in consolidation.py decide whether these become one lesson or three
# unrelated ones, and both were measured against the real functions before
# these were written:
#
#   text_similarity  >= CLUSTER_SIMILARITY (0.34), Jaccard on content tokens.
#     Officers writing the SAME lesson in wildly different words do not cluster.
#     A first draft of these -- 25-word paragraphs, each phrased freshly --
#     scored 0.07-0.15 and produced three clusters of one, so nothing formed.
#     Real corrections repeat the domain's vocabulary; these do too.
#
#   facet_compatible >= CLUSTER_FACET_OVERLAP (0.5), overlap coefficient.
#     This is the one that surprises: the intuition "vary everything so the
#     shared channel is the only thing left in scope" gives 1/5 = 0.2 overlap,
#     and corrections that share only the channel are treated as different
#     KINDS of case and never combine. The scope cannot be narrowed below what
#     the clustering gate will hold together.
#
# So these share three families (dsa / low FOIR / documented income) and vary
# product and ticket size: 3/5 = 0.6 overlap, and the clause comes out scoped
# to exactly those three.
LESSONS = [
    ("priya.nair@acme-bank.com", "LAN-2026-011768",
     "DSA sourced file, so verify employment with the employer before approval."),
    ("arjun.mehta@acme-bank.com", "LAN-2026-005671",
     "DSA sourced file. Verify employment with the employer before we approve this."),
    ("fatima.sheikh@acme-bank.com", "LAN-2026-010517",
     "DSA sourced again, verify employment directly with the employer before approval."),
    ("rahul.desai@acme-bank.com", "LAN-2026-009114",
     "DSA sourced file, verify employment with the employer before approving it."),
    ("meera.iyer@acme-bank.com", "LAN-2026-009323",
     "DSA sourced file again, so verify employment with the employer before approval."),
]

# Not corrected. Used to show the clause changes the NEXT decision rather than
# only describing the ones it was taught from.
HELD_OUT_DSA = "LAN-2026-005351"
# Not DSA. The half that proves the scope is real -- if this diverts too, the
# app has just become generally more cautious, which is a different (worse)
# thing than having learned something specific.
#
# Chosen to differ from HELD_OUT_DSA in the CHANNEL AND NOTHING ELSE: same
# product (auto), same amount band, same FOIR band, same income-proof state. A
# first pick (LAN-2026-000205) sat at FOIR 31.36 and so differed in two
# families at once -- had the clause not fired on it, the result could not tell
# you whether the channel or the FOIR band was responsible, which is no control
# at all.
CONTROL_BRANCH = "LAN-2026-000276"


def _token(subject: str) -> str:
    import jwt
    secret = os.getenv("JWT_SECRET") or ""
    if not secret:
        raise SystemExit("JWT_SECRET is not set - cannot mint an officer token.")
    now = int(time.time())
    return jwt.encode({
        "sub": subject, "user_id": subject, "email": subject,
        "org_id": ORG, "tenant_id": ORG,
        "roles": ["user", "org_admin"],
        "iss": "Citra-AI", "iat": now, "exp": now + 7200,
    }, secret, algorithm="HS256")


def _call(method: str, path: str, token: str, body: dict | None = None,
          timeout: int = 300) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


def _fetch_rows(app_ids: list[str]) -> dict[str, dict]:
    """Read the applications through the department MCP.

    Deliberately NOT a direct psycopg2 connection: smart-app-service has no
    Postgres driver (nothing there talks to a customer database directly -- the
    MCP is the only door), and adding one would put a second, ungoverned read
    path next to the one the product actually uses.

    The WHOLE row goes into the run, not just the id: case facets are derived
    from the RECORD, so a run given only an id yields `__unknown` for every
    family. The correction is still stored, but its facets match nothing and no
    clause can ever be scoped from it -- and nothing anywhere says so.
    """
    mcp = os.getenv("ACME_MCP_URL", "http://citra-ds-mcp-demo-acme-bank:8090")
    key = os.getenv("MCP_API_KEY") or ""
    if not key:
        raise SystemExit("MCP_API_KEY is not set - cannot read the system of record.")
    ids = ",".join("'" + a.replace("'", "") + "'" for a in app_ids)
    body = {
        "source_id": "loan_origination",
        "dataset_id": "loan_origination.loan_applications",
        "kind": "sql",
        "query": f"SELECT * FROM loan_applications WHERE application_id IN ({ids})",
        "row_limit": 50,
    }
    req = urllib.request.Request(
        f"{mcp}/run_query", data=json.dumps(body).encode(), method="POST",
        # Two credentials, deliberately: the API key is the SERVICE guard, and
        # X-User-JWT carries who is asking so the MCP can apply the same
        # visibility rules a real read gets. Sending only the key is a 401.
        headers={"Content-Type": "application/json", "X-API-Key": key,
                 "Authorization": f"Bearer {key}",
                 "X-User-JWT": _token("admin@citra-ai.com")})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"MCP /run_query -> {e.code}: "
                         f"{e.read()[:300].decode(errors='replace')}")
    rows = payload.get("rows") or payload.get("data") or []
    out = {r["application_id"]: r for r in rows if r.get("application_id")}
    missing = [a for a in app_ids if a not in out]
    if missing:
        raise SystemExit(f"not in the system of record: {', '.join(missing)}")
    return out



def _run_and_correct(officer: str, row: dict, reason: str) -> bool:
    token = _token(officer)
    who = officer.split("@")[0]
    log.info("  %-16s reviewing %s (%s, %s)", who, row["application_id"],
             row.get("product"), row.get("sourcing_channel"))
    res = _call("POST", f"/apps/{SLUG}/run", token,
                {"action": "review_application", "inputs": row})
    cid = res.get("correlation_id")
    if not cid:
        log.error("  %-16s no correlation_id - cannot correct", who)
        return False
    _call("POST", f"/apps/{SLUG}/run/{cid}/approve", token,
          {"decision": "reject", "decision_reason": reason}, timeout=120)
    log.info("  %-16s corrected it", who)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Form a learned clause in the acme-bank demo.")
    ap.add_argument("--officers", type=int, default=3,
                    help="How many officers correct (3 is the promotion threshold).")
    ap.add_argument("--skip-effect", action="store_true",
                    help="Skip the held-out / control runs at the end.")
    ap.add_argument("--effect-only", action="store_true",
                    help="Only run the held-out / control check against the "
                         "clause that already exists. Teaching again would add "
                         "a second set of corrections for the same lesson.")
    a = ap.parse_args()

    if a.officers < 3:
        log.warning("Fewer than 3 officers: the clause will stay a CANDIDATE, "
                    "not become active. That is the real behaviour, not a bug.")

    lessons = [] if a.effect_only else LESSONS[:a.officers]
    ids = [app_id for _, app_id, _ in lessons]
    if not a.skip_effect:
        ids += [HELD_OUT_DSA, CONTROL_BRANCH]
    rows = _fetch_rows(ids)

    admin_probe = _token("admin@citra-ai.com")
    if a.effect_only:
        clauses = _call("GET", f"/apps/{SLUG}/memory/clauses", admin_probe).get("clauses") or []
        if not [c for c in clauses if c.get("status") == "active"]:
            log.error("No ACTIVE clause to test. Run without --effect-only first.")
            return 1
    else:
        log.info("Teaching from %d correction(s), one per officer:", len(lessons))
        done = sum(_run_and_correct(o, rows[i], r) for o, i, r in lessons)
        if done < 3:
            log.error("Only %d correction(s) landed; 3 distinct officers are needed "
                      "for an ACTIVE clause. Stopping before consolidation.", done)
            return 1

    # Consolidation also runs on a timer (CONSOLIDATION_INTERVAL_SECONDS,
    # default 900). Calling it directly avoids a quarter-hour wait that makes
    # this look flaky when it is merely scheduled.
    admin = admin_probe
    if not a.effect_only:
        log.info("Running consolidation...")
        _call("POST", "/admin/consolidation/run", admin, {}, timeout=300)

    clauses = _call("GET", f"/apps/{SLUG}/memory/clauses", admin).get("clauses") or []
    if not clauses:
        log.error("No clause formed. The corrections are stored - check that "
                  "their case_facets are not all __unknown (a run given only an "
                  "id, rather than the whole row, produces exactly that).")
        return 1

    for c in clauses:
        log.info("Clause %s [%s]  scope=%s  officers=%d",
                 c.get("clause_id", "?"), c.get("status"),
                 c.get("scope_facets"), c.get("support_count", 0))
        log.info("  %s", (c.get("text") or "")[:300])

    active = [c for c in clauses if c.get("status") == "active"]
    if not active:
        log.warning("Clause(s) formed but none ACTIVE - fewer than %d distinct "
                    "officers agreed.", 3)

    if not a.skip_effect and active:
        log.info("Does it change the next decision?")
        tok = _token("priya.nair@acme-bank.com")
        for label, app_id in (("held-out DSA", HELD_OUT_DSA),
                              ("branch control", CONTROL_BRANCH)):
            res = _call("POST", f"/apps/{SLUG}/run", tok,
                        {"action": "review_application", "inputs": rows[app_id]})
            cited = res.get("cited_clauses") or []
            log.info("  %-15s %s -> %d clause(s) cited", label, app_id, len(cited))
        log.info("The control is the point: if the branch case cites the clause "
                 "too, the app got cautious generally rather than learning "
                 "something specific about DSA files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
