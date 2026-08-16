"""L1 — Auto-process RUNTIME GATE matrix (deterministic; no LLM/builder/services).
One small assertion per criterion. See docs/auto-process-test-plan.md."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import models
import auto_process as ap

def C(**k): return models.Condition(**k)
def P(**k): return models.AutoProcessPolicy(**k)
def cap(mx): return models.ValueCap(field="payload.amount", max=mx)
def g(pol, pw, row=None, result=None):
    return ap.gate_planned_write(pol, pw, ap.build_ctx(pw, row=row, result=result))[0]

R = []
def chk(cid, got, expect):
    R.append((cid, got == expect, got, expect))

thr = P(auto_commit_when=C(field="payload.amount", op="<", value=10000))
W = lambda amt=8000, conf=None: {"payload": {"amount": amt}, "_result": {"confidence": conf}}

# R1 recommend trigger is not gated (no policy, default mode)
t = models.Trigger(id="t", type="poll", action="x", tool="list")
chk("R1 recommend-default", (t.execution_mode, t.auto_process_policy), ("recommend", None))
# R2/R3 threshold
chk("R2 threshold-pass", g(thr, W(amt=8000)), "commit")
chk("R3 threshold-fail", g(thr, W(amt=12000)), "recommend")
# R4/R5 allowed-set
aset = P(auto_commit_when=C(field="payload.team", op="in", value=["it", "support"]))
chk("R4 set-pass", g(aset, {"payload": {"team": "it"}}), "commit")
chk("R5 set-fail", g(aset, {"payload": {"team": "finance"}}), "recommend")
# R7/R8 confidence
cpol = P(auto_commit_when=C(field="payload.amount", op="<", value=10000), confidence_min=0.8)
chk("R7 conf-below", g(cpol, W(amt=8000, conf=0.6)), "recommend")
chk("R8 conf-ok", g(cpol, W(amt=8000, conf=0.9)), "commit")
# R9 value cap
vpol = P(auto_commit_when=C(always=True), value_cap=cap(10000))
chk("R9 value-cap", g(vpol, {"payload": {"amount": 12000}}), "recommend")
# R11 cap + confidence pass
fpol = P(auto_commit_when=C(field="payload.amount", op="<", value=10000),
         value_cap=cap(10000), confidence_min=0.8)
chk("R11 cap-conf-pass", g(fpol, W(amt=8000, conf=0.9)), "commit")
# R12 always-safe
allp = P(auto_commit_when=C(always=True))
chk("R12 always-safe", g(allp, W()), "commit")
# R14 always + low confidence
allc = P(auto_commit_when=C(always=True), confidence_min=0.8)
chk("R14 always+low-conf", g(allc, W(conf=0.5)), "recommend")
# R15 no auto_commit_when -> recommend (no gate = never auto-commit; fail-closed)
chk("R15 no-gate-recommend", g({}, W()), "recommend")
# R16 unknown field fails closed
upol = P(auto_commit_when=C(field="row.nonexistent", op="==", value=1))
chk("R16 unknown-field-failclosed", g(upol, {"payload": {}}, row={}), "recommend")
# R17 nested any/not
npol = P(auto_commit_when=C(**{"any": [
    {"field": "row.severity", "op": "==", "value": 1},
    {"not": {"field": "payload.amount", "op": ">", "value": 100000}}]}))
chk("R17 nested", g(npol, {"payload": {"amount": 5000}}, row={"severity": 2}), "commit")
# R18 row from inputs
rpol = P(auto_commit_when=C(field="row.severity", op="<=", value=3))
chk("R18 row-from-inputs", g(rpol, {"payload": {}}, row={"severity": 2}), "commit")

# R19/R20 partition + max_auto (simulate the trigger gate loop)
def partition(pol, writes, max_auto, row=None):
    commit = stage = 0
    for w in writes:
        d = g(pol, w, row=row)
        if d == "commit" and commit < max_auto: commit += 1
        else: stage += 1
    return commit, stage
wp = {"payload": {"amount": 100}}
wf = {"payload": {"amount": 99999}}
chk("R19 max-auto-cap", partition(thr, [wp, wp, wp], 2), (2, 1))
chk("R20 partition-mixed", partition(thr, [wp, wf, wp], 50), (2, 1))

for cid, ok, got, exp in R:
    print(f"  [{'PASS' if ok else 'FAIL'}] {cid:28} got={got!r} expect={exp!r}")
fails = [c for c, ok, _, _ in R if not ok]
print(f"\nL1 RUNTIME MATRIX: {len(R)-len(fails)}/{len(R)} passed" + ("" if not fails else f"  FAILED: {fails}"))
sys.exit(1 if fails else 0)
