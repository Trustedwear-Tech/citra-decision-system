"""Screening Health stats + officer-verdict stamp (admin panel plan §1–§3).

Pins the metric definitions the admin card renders — a re-screened case counts
ONCE (latest state); false-alarm rate divides by JUDGED cases only; advisories
fire only past the (≥5 verdicts, ≥70% dismissal) thresholds — and the stamp's
miss behavior (no screening row → False, loudly, never an exception)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import fraud_synthesis as fs


def _run(coro):
    return asyncio.run(coro)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_a, **_k):
        return self

    async def to_list(self, length=None):
        return self._rows[:length]


class _FakeCol:
    name = "smartapp_fraud_screenings"

    def __init__(self, rows):
        self.rows = rows
        self.updated = None

    def find(self, _match, _proj=None):
        return _FakeCursor(self.rows)

    async def find_one_and_update(self, match, update, sort=None):
        for r in self.rows:
            if r.get("record_id") == match.get("record_id"):
                r.update(update["$set"])
                self.updated = r
                return r
        return None

    async def create_index(self, *_a, **_k):
        return None


def _row(record_id, app="theft-triage", points=0, breakdown=None,
         verdict=None, reason=None, age_min=0):
    return {
        "app_slug": app, "record_id": record_id, "points": points,
        "breakdown": breakdown or {}, "gated": points < 2,
        "officer_verdict": verdict, "verdict_reason": reason,
        "verdict_by": "officer-1" if verdict else None,
        "verdict_at": datetime.now(timezone.utc) if verdict else None,
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=age_min),
    }


@pytest.fixture
def patched_col(monkeypatch):
    holder = {}

    def _install(rows):
        col = _FakeCol(rows)
        monkeypatch.setattr(fs, "_screenings_col", lambda: col)

        async def _noop():
            return None
        monkeypatch.setattr(fs, "_ensure_scr_indexes", _noop)
        holder["col"] = col
        return col

    return _install


# ── latest-per-record dedupe ─────────────────────────────────────────────────
def test_rescreened_case_counts_once_at_latest_state(patched_col):
    # Same record twice (rows arrive newest-first): only the newest counts.
    patched_col([
        _row("R1", points=3, verdict="dismissed", reason="valid tenant change", age_min=0),
        _row("R1", points=3, age_min=60),
        _row("R2", points=0, age_min=5),
    ])
    out = _run(fs.screening_stats(tenant_id="t1"))
    assert out["totals"] == {"screened": 2, "warned": 1, "confirmed": 0,
                             "dismissed": 1, "false_alarm_rate": 1.0}


# ── rate definition: dismissed ÷ judged, unjudged warnings don't dilute ──────
def test_false_alarm_rate_divides_by_judged_only(patched_col):
    patched_col([
        _row("R1", points=3, verdict="confirmed"),
        _row("R2", points=3, verdict="dismissed", reason="noise"),
        _row("R3", points=3),   # warned but never judged
        _row("R4", points=3),
    ])
    out = _run(fs.screening_stats(tenant_id="t1"))
    assert out["totals"]["warned"] == 4
    assert out["totals"]["false_alarm_rate"] == 0.5  # 1/(1+1), NOT 1/4


def test_org_shape_and_per_app_rows(patched_col):
    patched_col([
        _row("A1", app="theft", points=4, verdict="confirmed"),
        _row("A2", app="theft", points=2, verdict="dismissed", reason="noise"),
        _row("B1", app="claims", points=0),
    ])
    out = _run(fs.screening_stats(tenant_id="t1"))
    apps = {a["app_slug"]: a for a in out["apps"]}
    assert apps["theft"]["false_alarm_rate"] == 0.5
    assert apps["claims"] == {"app_slug": "claims", "screened": 1, "warned": 0,
                              "confirmed": 0, "dismissed": 0,
                              "false_alarm_rate": None}
    # org scope: no drill-down keys
    assert "signals" not in out and "advisories" not in out


# ── app drill-down: signals, reasons, advisories ─────────────────────────────
def test_app_scope_signal_breakdown_and_reasons(patched_col):
    patched_col([
        _row("R1", points=3, breakdown={"exact_duplicate": 1},
             verdict="dismissed", reason="same meter nameplate — expected"),
        _row("R2", points=3, breakdown={"exact_duplicate": 1}, verdict="confirmed"),
    ])
    out = _run(fs.screening_stats(tenant_id="t1", app_slug="theft-triage"))
    sig = out["signals"]["exact_duplicate"]
    assert sig["cases"] == 2 and sig["confirmed"] == 1 and sig["dismissed"] == 1
    assert sig["label"].startswith("Reused evidence")
    assert out["dismissal_reasons"][0]["reason"].startswith("same meter")
    assert out["advisories"] == []  # only 2 verdicts — below the ≥5 threshold


def test_advisory_fires_past_thresholds(patched_col):
    rows = [
        _row(f"R{i}", points=3, breakdown={"exif_gps_far_from_claim": 1},
             verdict="dismissed", reason="premise coords imprecise")
        for i in range(6)
    ] + [_row("R9", points=3, breakdown={"exif_gps_far_from_claim": 1},
              verdict="confirmed")]
    patched_col(rows)
    out = _run(fs.screening_stats(tenant_id="t1", app_slug="theft-triage"))
    assert len(out["advisories"]) == 1
    adv = out["advisories"][0]
    assert adv["signal"] == "exif_gps_far_from_claim"
    assert adv["dismissal_rate"] == round(6 / 7, 3)
    assert "gps_radius_km" in adv["advisory"]  # names the exact lever


def test_advisory_respects_dismissal_rate_floor(patched_col):
    # 3 dismissed / 6 judged = 50% < 70% → no advisory even with volume.
    rows = (
        [_row(f"C{i}", points=3, breakdown={"metadata_anomaly": 1},
              verdict="confirmed") for i in range(3)]
        + [_row(f"D{i}", points=3, breakdown={"metadata_anomaly": 1},
                verdict="dismissed", reason="scanner stamps editor") for i in range(3)]
    )
    patched_col(rows)
    out = _run(fs.screening_stats(tenant_id="t1", app_slug="theft-triage"))
    assert out["advisories"] == []


def test_sampled_zero_point_dismissal_does_not_inflate_rate(patched_col):
    # Review fix: a points=0 case escalated by the random audit sample can be
    # dismissed by the officer; it must NOT count in the false-alarm rate,
    # whose UI definition is "false alarms ÷ warnings officers judged".
    patched_col([
        _row("R1", points=3, verdict="confirmed"),
        _row("R2", points=0, verdict="dismissed", reason="sampled clean case"),
    ])
    out = _run(fs.screening_stats(tenant_id="t1"))
    assert out["totals"]["warned"] == 1
    assert out["totals"]["dismissed"] == 0     # points=0 verdict excluded
    assert out["totals"]["false_alarm_rate"] == 0.0


def test_tenant_id_accepts_candidate_list(patched_col):
    # Review fix: an admin JWT may carry the app tenant in either org_id or
    # tenant_id — the org rollup passes both candidates and the match must
    # become an $in (verified via the captured match filter).
    col = patched_col([_row("R1", points=3)])
    captured = {}
    orig_find = col.find

    def _find(match, proj=None):
        captured.update(match)
        return orig_find(match, proj)

    col.find = _find
    _run(fs.screening_stats(tenant_id=["org-a", "dept-b"]))
    assert captured["tenant_id"] == {"$in": ["org-a", "dept-b"]}


# ── the verdict stamp ────────────────────────────────────────────────────────
def test_stamp_updates_matching_screening(patched_col):
    col = patched_col([_row("R1", points=3)])
    ok = _run(fs.stamp_officer_verdict(
        tenant_id="t1", app_slug="theft-triage", record_id="R1",
        verdict="dismissed", reason="false alarm", actor="officer-9"))
    assert ok is True
    assert col.updated["officer_verdict"] == "dismissed"
    assert col.updated["verdict_by"] == "officer-9"


def test_stamp_miss_returns_false_not_raise(patched_col):
    patched_col([])
    ok = _run(fs.stamp_officer_verdict(
        tenant_id="t1", app_slug="theft-triage", record_id="NOPE",
        verdict="confirmed", reason=None, actor=None))
    assert ok is False


def test_stamp_rejects_bad_verdict(patched_col):
    patched_col([])
    with pytest.raises(ValueError):
        _run(fs.stamp_officer_verdict(
            tenant_id="t1", app_slug="s", record_id="R",
            verdict="maybe", reason=None, actor=None))


# ── every gate-scored signal key has an advisory entry ───────────────────────
def test_advisory_map_covers_all_scored_signal_keys():
    scored = set(fs._POINTS_DEFAULTS) | {"severity_mismatch", "severity_warn"}
    missing = scored - set(fs.SIGNAL_ADVISORIES)
    assert not missing, f"signal keys without an advisory/label: {missing}"
