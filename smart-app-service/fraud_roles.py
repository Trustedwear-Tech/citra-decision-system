"""Ontology-driven artifact roles — the missing context layer for reuse signals.

The reuse detectors (SHA-256 exact-dup, dHash near-dup, image embeddings in
``fraud_checks``/``fraud_image_index``) answer ONE question: "has this artifact
been seen before?". Whether that is FRAUD depends entirely on what the artifact
IS — which the source ontology declares, not the detector:

  * ``identity`` artifact (a headshot / ID scan / signature specimen) — reuse is
    EXPECTED (same subject across applications). A student re-applying six months
    later with the same photo is not fraud; the match is *verification*.
  * ``evidence`` artifact (an accident photo / damage shot / inspection-defect
    image / receipt) — reuse across DISTINCT cases is the classic double-dip:
    recycled proof. THIS is the fraud signal.
  * ``supporting`` artifact (a brochure, a generic T&C PDF) — reuse is
    meaningless; ignore it.

So the same "seen before" bit scores 0 for an identity column and +N for an
evidence column. The role comes from ``sources.json`` (per-url-column
``artifact_role`` / ``reuse_policy``), rides the catalogue, and the builder
auto-wires it onto the consistency_check tool as ``url_column_roles``.

Doctrine (matches the fraud plan): explainable-only — every verdict carries the
WHY; deterministic + zero-LLM; a signal is EVIDENCE on a recommendation, never
an auto-reject. SAFE DEFAULT: when the ontology says nothing, treat the artifact
as ``evidence``/``suspicious`` — i.e. preserve today's flag-every-duplicate
behavior. The ontology only ever OPTS A COLUMN OUT (marks it identity/supporting);
it can never silently weaken screening.

WHAT THE ONTOLOGY ACTUALLY DRIVES (be precise — this module used to claim "the
ontology drives the whole reuse check, not the LLM", which overstated it):

  ONTOLOGY-DRIVEN (this module):
    * whether a dataset is screened at all      (``screening_active``)
    * WHICH artifact columns are fingerprinted  (``_fingerprint_targets``)
    * HOW a reuse hit is read                   (``artifact_role``/``reuse_policy``)
    * auto-CREATION of a screen                 (zero builder-LLM judgment)
    * WHICH identifiers link cases              (``identity_fields``)
    * whether a DATASET's artifacts are fingerprinted at all — the per-item gate
      (``tools_v2_dispatch._dataset_fraud_active``) resolves each tool's OWN
      data_source_id, so an unscreened dataset is never fingerprinted just because
      a sibling dataset in the same app opted in.

  NOT ontology-driven (deliberate, but do not mistake it for ontology):
    * ``gate_min_points`` / ``sample_rate`` — builder-authored on the
      fraud_synthesis tool; they decide whether a case earns a reasoning pass.
    * ``field_types`` / ``link_entities`` — builder-authored.
    * signal WEIGHTS (``FRAUD_SIGNAL_WEIGHTS``) — env, tuned by a human from the
      L3 calibration report.
    * a hand-authored screen on a dataset with NO fraud ontology is preserved
      (silence ≠ opt-out), and human-added ``url_columns`` survive autowire — so a
      legacy screen can still turn a dataset on without the source asking for it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ARTIFACT_ROLES = frozenset({"identity", "evidence", "supporting", "payment_proof"})
REUSE_POLICIES = frozenset({"expected", "suspicious", "ignore"})

# Role → its default reuse policy when the column doesn't override it explicitly.
# payment_proof reuses like evidence (the same receipt clearing two accounts IS
# a signal) and additionally pins the E4 ledger check to its column.
_ROLE_DEFAULT_POLICY = {
    "identity": "expected",
    "evidence": "suspicious",
    "supporting": "ignore",
    "payment_proof": "suspicious",
}

# When the ontology is silent, an artifact is treated as EVIDENCE/SUSPICIOUS.
# This preserves the pre-ontology behavior (every duplicate is a signal); the
# ontology can only relax it by naming a column identity/supporting.
DEFAULT_ROLE = "evidence"
DEFAULT_POLICY = "suspicious"

SIGNAL_KEY_EXACT = "reused_artifact_exact"
SIGNAL_KEY_NEAR = "reused_artifact_near"


# NOTE ON SCORING: this module decides WHETHER a reuse hit counts (is_fraud_signal)
# and explains WHY; it deliberately does NOT compute severity points. The T3 gate
# (fraud_synthesis.severity_points) is the single scorer — it walks the raw
# fingerprint keys (``duplicate`` → exact_duplicate weight, ``phash_near_dups`` →
# phash_near_dup × capped-count) and honours the FRAUD_SIGNAL_WEIGHTS env override.
# A per-artifact points number here could never match that count-based, env-tunable
# total, so we don't publish one (it would only drift from what actually escalates).


def effective_reuse_policy(
    artifact_role: Optional[str] = None,
    reuse_policy: Optional[str] = None,
) -> str:
    """The reuse policy actually in force: an explicit ``reuse_policy`` wins;
    otherwise it's derived from ``artifact_role``; otherwise the safe default."""
    if reuse_policy in REUSE_POLICIES:
        return reuse_policy
    role = artifact_role if artifact_role in ARTIFACT_ROLES else DEFAULT_ROLE
    return _ROLE_DEFAULT_POLICY.get(role, DEFAULT_POLICY)


def interpret_reuse(
    *,
    artifact_role: Optional[str] = None,
    reuse_policy: Optional[str] = None,
    exact_duplicate: bool = False,
    near_duplicate: bool = False,
) -> Dict[str, Any]:
    """Turn a raw 'seen before' hit into an explainable, role-aware signal.

    Returns a dict the runtime attaches to the artifact finding (the case
    synthesizer does its OWN scoring off the raw keys — see the scoring note above):
      is_fraud_signal · signal_key · artifact_role · policy · label · why
    """
    policy = effective_reuse_policy(artifact_role, reuse_policy)
    role = artifact_role if artifact_role in ARTIFACT_ROLES else DEFAULT_ROLE

    base = {
        "artifact_role": role,
        "policy": policy,
        "is_fraud_signal": False,
        "signal_key": None,
        "label": None,
        "why": None,
    }
    if not (exact_duplicate or near_duplicate):
        return base

    precision = "exact" if exact_duplicate else "near"

    if policy == "expected":
        # A match on an identity/reference artifact is verification, not fraud.
        return {
            **base,
            "label": "identity match",
            "why": (
                f"This {role} artifact matches one already on file — for an "
                "identity/reference artifact that is EXPECTED (same subject) and "
                "is not a fraud signal. It corroborates the applicant's identity."
            ),
        }
    if policy == "ignore":
        return base

    # policy == "suspicious"
    kind = (
        "byte-identical to" if precision == "exact"
        else "a near-duplicate (recompressed / cropped / re-shot) of"
    )
    if role == "payment_proof":
        return {
            **base,
            "is_fraud_signal": True,
            "signal_key": SIGNAL_KEY_EXACT if precision == "exact" else SIGNAL_KEY_NEAR,
            "label": "reused payment proof",
            "why": (
                f"This payment-proof document is {kind} one already on file for a "
                "DIFFERENT case — the same receipt cannot clear more than one "
                "account."
            ),
        }
    return {
        **base,
        "is_fraud_signal": True,
        "signal_key": SIGNAL_KEY_EXACT if precision == "exact" else SIGNAL_KEY_NEAR,
        "label": "reused evidence",
        "why": (
            f"This {role} artifact is {kind} one already on file for a DIFFERENT "
            "case — recycling evidence across cases is a classic double-dip / "
            "resubmission fraud signal."
        ),
    }


# The raw fingerprint keys emitted by ``fraud_checks.artifact_flags`` — the
# exact-duplicate flag and the near-duplicate list. Kept as named constants so
# the dispatcher and this module read the SAME contract (the previous drift —
# reading ``exact_duplicate`` while the flag was emitted as ``duplicate`` — made
# the whole exact-dup path dead).
RAW_EXACT_KEY = "duplicate"
RAW_NEAR_KEY = "phash_near_dups"
# Document content near-dup (text SimHash) — the doc twin of phash_near_dups. It is
# a REUSE tier, so it MUST obey artifact_role exactly like the pixel tiers: a
# re-issued identity document (an ID scan the same applicant legitimately resubmits)
# is verification, not fraud, and must never reach the gate.
RAW_TEXT_NEAR_KEY = "text_near_dups"


def apply_reuse_signal(
    finding: Dict[str, Any],
    *,
    artifact_role: Optional[str] = None,
    reuse_policy: Optional[str] = None,
) -> str:
    """Annotate ONE artifact finding in place with role-aware reuse semantics and
    return its classification: ``"fraud"`` | ``"identity"`` | ``"none"``.

    Reuse can surface in FOUR tiers, all of which ``fraud_checks.artifact_flags``
    may attach and all of which the T3 gate scores:
      * SHA-256 exact   → ``duplicate: True``
      * dHash near      → ``phash_near_dups: [...]``          (images)
      * CLIP embedding  → ``image_index.near_duplicates`` / ``image_index.similar``
      * text SimHash    → ``text_near_dups: [...]``           (documents)

    For a NON-fraud hit (identity match, or a ``supporting``/``ignore`` artifact)
    this STRIPS every one of those markers off the finding — otherwise the gate
    (fraud_synthesis.severity_points), which walks the raw keys anywhere in the
    blob, would still score an identity match and the role exemption would be
    cosmetic. Metadata anomalies are NOT stripped — tampering is role-independent."""
    img = finding.get("image_index")
    img = img if isinstance(img, dict) else None
    exact = bool(finding.get(RAW_EXACT_KEY))
    near = (
        bool(finding.get(RAW_NEAR_KEY))
        or bool(finding.get(RAW_TEXT_NEAR_KEY))
        or bool(img and img.get("near_duplicates"))
    )
    has_similar = bool(img and img.get("similar"))

    sig = interpret_reuse(
        artifact_role=artifact_role, reuse_policy=reuse_policy,
        exact_duplicate=exact, near_duplicate=near,
    )
    finding["artifact_role"] = sig["artifact_role"]
    finding["reuse_policy"] = sig["policy"]
    finding["reuse_is_fraud_signal"] = sig["is_fraud_signal"]
    if sig["signal_key"]:
        finding["reuse_signal_key"] = sig["signal_key"]
    if sig["label"]:
        finding["reuse_label"] = sig["label"]
        finding["reuse_explanation"] = sig["why"]

    if sig["is_fraud_signal"]:
        return "fraud"                          # raw keys KEPT — the gate scores them

    # We ONLY strip reuse markers for a role that says reuse is NOT suspicious —
    # identity (verification) and supporting/ignore (meaningless). A `suspicious`
    # artifact is NEVER stripped: even a weak CLIP `similar`-only hit is a real
    # gate signal (clip_similar) and removing it would silently WEAKEN the default
    # no-ontology path (the ontology may only relax screening, never weaken it).
    if sig["policy"] == "suspicious":
        return "none"                           # weak/no strong hit; leave the blob for the gate

    if not (exact or near or has_similar):
        return "none"

    # policy is 'expected' (identity) or 'ignore' (supporting): strip EVERY
    # gate-scored reuse marker across all three tiers so the exemption reaches the
    # gate, not just the tool summary.
    moved_exact = finding.pop(RAW_EXACT_KEY, None)
    moved_near = finding.pop(RAW_NEAR_KEY, None)
    moved_text = finding.pop(RAW_TEXT_NEAR_KEY, None)
    clip_near = img.pop("near_duplicates", None) if img else None
    if img:
        img.pop("similar", None)
    if sig["policy"] == "expected" and (moved_exact or moved_near or moved_text or clip_near):
        # Identity match = verification. Preserve the fact for the officer under
        # informational keys the gate walker does not score.
        if moved_exact:
            finding["identity_match_exact"] = True
        if moved_near or moved_text or clip_near:
            finding["identity_match_near"] = True
        return "identity"
    return "none"


def role_for_url_column(
    agent_spec: Any,
    *,
    tool: Any = None,
    data_source_id: Optional[str],
    url_column: Optional[str],
) -> Dict[str, Optional[str]]:
    """The ontology ``(artifact_role, reuse_policy)`` for ONE media column.

    Resolution order:
      1. The tool's OWN stamped role — ``autowire_fraud_roles`` writes
         ``artifact_role`` / ``reuse_policy`` straight onto the ``image_analyze`` /
         ``doc_extract`` tool from ``sources.json``. This is authoritative and needs
         no sibling: an ``identity`` artifact on a dataset with no primary key (so no
         auto-created ``consistency_check``) is still read as verification, not fraud.
      2. Fallback — the sibling ``consistency_check`` on the same ``data_source_id``
         that lists the column in ``url_column_roles`` (covers apps published before
         the stamp shipped, so no republish is required for the common case).

    Returns the safe default ``{None, None}`` (⇒ evidence/suspicious at read time)
    when neither source names the column — preserving flag-everything, so the
    ontology can only ever RELAX this path, never silently weaken it."""
    default: Dict[str, Optional[str]] = {"artifact_role": None, "reuse_policy": None}
    # 1. The tool's own stamped role (authoritative, sibling-independent).
    if tool is not None:
        own_role = _tool_attr(tool, "artifact_role")
        own_policy = _tool_attr(tool, "reuse_policy")
        if own_role or own_policy:
            return {"artifact_role": own_role, "reuse_policy": own_policy}
    if not (data_source_id and url_column):
        return default
    # 2. Fallback: read the role off the sibling consistency_check.
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        if _tool_attr(t, "kind") != "consistency_check":
            continue
        if _tool_attr(t, "data_source_id") != data_source_id:
            continue
        roles = _tool_attr(t, "url_column_roles") or {}
        r = roles.get(url_column)
        if isinstance(r, dict) and r:
            return {
                "artifact_role": r.get("artifact_role"),
                "reuse_policy": r.get("reuse_policy"),
            }
    return default


def resolve_column_roles(
    url_columns: Optional[List[str]],
    url_column_roles: Optional[Dict[str, Dict[str, str]]],
) -> Dict[str, Dict[str, Optional[str]]]:
    """Per url column, the (artifact_role, reuse_policy) actually in force —
    filling the safe default for any column the ontology didn't annotate."""
    roles = url_column_roles or {}
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for col in (url_columns or []):
        r = roles.get(col) or {}
        role = r.get("artifact_role")
        out[col] = {
            "artifact_role": role if role in ARTIFACT_ROLES else DEFAULT_ROLE,
            "reuse_policy": r.get("reuse_policy"),  # None ⇒ derive from role
        }
    return out


def _tool_attr(tool: Any, key: str, default: Any = None) -> Any:
    return tool.get(key, default) if isinstance(tool, dict) else getattr(tool, key, default)


def _tool_set(tool: Any, key: str, value: Any) -> None:
    if isinstance(tool, dict):
        tool[key] = value
    else:
        setattr(tool, key, value)


def _applies_value(fraud_screening: Any) -> Optional[bool]:
    """Read ``fraud_screening.applies`` whether the block arrives as a dict (the
    common catalogue case) or a pydantic model. TRISTATE: True / False / None
    (None = the key was omitted, which is NOT the same as an explicit False)."""
    if isinstance(fraud_screening, dict):
        return fraud_screening.get("applies")
    if fraud_screening is not None:
        return getattr(fraud_screening, "applies", None)
    return None


def _identity_fields(fraud_screening: Any) -> List[str]:
    """``fraud_screening.identity_fields`` — the columns the source declares as
    cross-record linkable keys. Dict- or model-shaped (same dual read as
    ``_applies_value``); always a list, never None."""
    if isinstance(fraud_screening, dict):
        vals = fraud_screening.get("identity_fields")
    elif fraud_screening is not None:
        vals = getattr(fraud_screening, "identity_fields", None)
    else:
        return []
    return [str(v) for v in (vals or []) if v]


def _column_names(entry: Dict[str, Any]) -> set:
    """The catalogue entry's column-name set (name == physical by pipeline
    invariant; physical_name is the fallback for hand-built fixtures). One
    definition for both autowire call sites so the 'unknown column' fail-loud
    validation can never behave differently on first-publish vs republish."""
    return {
        c.get("name") or c.get("physical_name")
        for c in (entry.get("columns") or []) if isinstance(c, dict)
    }


def claim_context_from_screening(
    fraud_screening: Any,
    dataset_kind: Optional[str],
    column_names: Optional[set] = None,
    domain: Any = None,
) -> Optional[Dict[str, Any]]:
    """The EXIF↔claim comparator config (E1) the ontology declares —
    ``incident_date_field`` / ``location_lat_field`` / ``location_lon_field`` /
    ``gps_radius_km`` — packaged for the runtime, with the dataset ``kind`` the
    structured read-by-key needs. Returns None when the ontology declares
    nothing (⇒ no check runs — ontology-driven end-to-end).

    FAIL LOUD on half-declarations: a lat without a lon (or vice versa) and a
    declared column that doesn't exist in the dataset are sources.json mistakes
    the author must hear about — the broken piece is dropped VISIBLY, never
    silently carried into a runtime that would no-op."""
    def _get(k: str) -> Any:
        if isinstance(fraud_screening, dict):
            return fraud_screening.get(k)
        return getattr(fraud_screening, k, None) if fraud_screening is not None else None

    def _known(col: Optional[str], what: str) -> Optional[str]:
        if col and column_names is not None and col not in column_names:
            logger.warning(
                "[fraud-ontology] fraud_screening.%s names column %r which does "
                "not exist in the dataset — check dropped; fix sources.json",
                what, col)
            return None
        return col

    date_f = _known(_get("incident_date_field"), "incident_date_field")
    lat_f = _known(_get("location_lat_field"), "location_lat_field")
    lon_f = _known(_get("location_lon_field"), "location_lon_field")
    if (lat_f is None) != (lon_f is None):
        logger.warning(
            "[fraud-ontology] fraud_screening declares only ONE of "
            "location_lat_field/location_lon_field — GPS check needs both; "
            "dropped; fix sources.json")
        lat_f = lon_f = None
    if not date_f and not lat_f:
        return None
    ctx: Dict[str, Any] = {"dataset_kind": dataset_kind}
    if date_f:
        ctx["incident_date_field"] = date_f
    if lat_f:
        ctx["location_lat_field"] = lat_f
        ctx["location_lon_field"] = lon_f
    radius = _get("gps_radius_km")
    if radius is None:
        # Ontology silent → the vertical pack may speak (an inspection is
        # premise-bound: 1 km, not the generic 10). Still None when no pack —
        # the comparator's platform default (10 km) applies at run time.
        radius = pack_default(sub_vertical_of(domain), "gps_radius_km")
    if radius is not None:
        ctx["gps_radius_km"] = radius
    return ctx


def payment_proof_from_screening(
    fraud_screening: Any,
    by_ref: Optional[Dict[str, Dict[str, Any]]] = None,
    dataset_columns: Optional[List[Dict[str, Any]]] = None,
    domain: Any = None,
) -> Optional[Dict[str, Any]]:
    """The E4 payment-proof config the ontology declares, packaged for the
    runtime with the ledger routing (source_id / dataset / kind) resolved from
    the catalogue. Returns None when undeclared (⇒ the check never runs).

    FAIL LOUD on mis-declarations: a ledger dataset the catalogue doesn't
    know, or a declared ledger column that doesn't exist, drops the config
    VISIBLY — a half-wired payment check that silently no-ops at runtime is
    exactly what this module exists to prevent.

    ``dataset_columns`` are the SCREENED dataset's columns: the check must be
    PINNED to the column(s) tagged ``artifact_role: payment_proof`` (a record
    can carry a payment receipt AND a purchase bill — matching the wrong one
    against the payment ledger yields a false 'reference not found'). No tagged
    column ⇒ the whole config is dropped, loudly."""
    if isinstance(fraud_screening, dict):
        pp = fraud_screening.get("payment_proof")
    elif fraud_screening is not None:
        pp = getattr(fraud_screening, "payment_proof", None)
    else:
        return None
    if pp is None:
        return None
    if not isinstance(pp, dict):
        pp = {k: getattr(pp, k) for k in (
            "ledger_dataset", "match_field", "amount_field", "date_field",
            "party_field", "doc_ref_field", "doc_amount_field",
            "doc_date_field", "doc_party_field", "amount_tolerance_pct",
            "date_window_days") if hasattr(pp, k)}

    ledger_ref = pp.get("ledger_dataset")
    match_field = pp.get("match_field")
    if not ledger_ref or not match_field:
        logger.warning(
            "[fraud-ontology] payment_proof needs both ledger_dataset and "
            "match_field — dropped; fix sources.json")
        return None
    entry = (by_ref or {}).get(ledger_ref)
    if entry is None:
        logger.warning(
            "[fraud-ontology] payment_proof.ledger_dataset %r is not in the "
            "catalogue — dropped; fix sources.json (or crawl the source)",
            ledger_ref)
        return None

    # F1 — pin the check to the tagged receipt column(s). The registry enforces
    # this pairing at authoring time; a catalogue that bypassed that validation
    # (hand-authored MCP) is dropped here with the same message, never run
    # unpinned against whichever document happens to be attached.
    doc_columns = [
        c.get("name") or c.get("physical_name")
        for c in (dataset_columns or [])
        if (c.get("artifact_role") == "payment_proof"
            and (c.get("name") or c.get("physical_name")))
    ]
    if not doc_columns:
        logger.warning(
            "[fraud-ontology] payment_proof declared but NO column is tagged "
            "artifact_role='payment_proof' — dropped; tag the receipt column in "
            "sources.json so the ledger check can never match a different "
            "attached bill (ledger %r)", ledger_ref)
        return None

    ledger_cols = _column_names(entry)
    cfg: Dict[str, Any] = {
        "ledger_source_id": str(ledger_ref).split(".", 1)[0],
        "ledger_dataset": ledger_ref,
        "ledger_kind": entry.get("kind"),
        "match_field": match_field,
        "doc_columns": doc_columns,
        "doc_ref_field": pp.get("doc_ref_field") or "transaction_ref",
        "doc_amount_field": pp.get("doc_amount_field") or "amount",
        "doc_date_field": pp.get("doc_date_field") or "payment_date",
    }
    # F2 — the ledger's own catalogue description rides the config so the
    # screen's tool description can tell the agent what the ledger IS FOR
    # (which bill matches which ledger).
    _ledger_desc = (entry.get("description") or "").strip()
    if _ledger_desc:
        cfg["ledger_description"] = _ledger_desc[:300]
    if match_field not in ledger_cols:
        logger.warning(
            "[fraud-ontology] payment_proof.match_field %r does not exist in "
            "ledger %r — dropped; fix sources.json", match_field, ledger_ref)
        return None
    for key in ("amount_field", "date_field", "party_field"):
        col = pp.get(key)
        if col and col not in ledger_cols:
            logger.warning(
                "[fraud-ontology] payment_proof.%s names column %r which does "
                "not exist in ledger %r — that comparison dropped; fix "
                "sources.json", key, col, ledger_ref)
            continue
        if col:
            cfg[key] = col
    if pp.get("doc_party_field"):
        cfg["doc_party_field"] = pp["doc_party_field"]
    # Tolerance resolution: ontology-explicit > vertical pack > platform
    # default. The pack layer is why the models upstream stopped defaulting
    # these at parse time (an early default silences the pack forever).
    _sv = sub_vertical_of(domain)
    for key, default in (("amount_tolerance_pct", 1.0), ("date_window_days", 3)):
        val = pp.get(key)
        if isinstance(val, (int, float)) and val >= 0:
            cfg[key] = val
        else:
            packed = pack_default(_sv, key)
            cfg[key] = packed if isinstance(packed, (int, float)) else default
    return cfg


def date_rules_from_screening(
    fraud_screening: Any,
    column_names: Optional[set] = None,
) -> Optional[List[Dict[str, Any]]]:
    """The E6 declarative date rules the ontology declares, validated against
    the dataset's columns. A rule naming a ghost column is dropped LOUDLY
    (the registry rejects it at authoring; this guards hand-authored
    catalogues). None when undeclared ⇒ no rule ever runs."""
    if isinstance(fraud_screening, dict):
        rules = fraud_screening.get("date_rules")
    elif fraud_screening is not None:
        rules = getattr(fraud_screening, "date_rules", None)
    else:
        return None
    if not rules:
        return None
    out: List[Dict[str, Any]] = []
    for raw in rules:
        r = raw if isinstance(raw, dict) else {
            k: getattr(raw, k) for k in (
                "name", "earlier_field", "later_field", "min_days_between",
                "max_days_between") if hasattr(raw, k)}
        name = r.get("name") or "date_rule"
        e_f, l_f = r.get("earlier_field"), r.get("later_field")
        if not (e_f and l_f):
            logger.warning(
                "[fraud-ontology] date_rules[%r] needs earlier_field and "
                "later_field — dropped; fix sources.json", name)
            continue
        if column_names is not None and column_names:
            missing = [f for f in (e_f, l_f) if f not in column_names]
            if missing:
                logger.warning(
                    "[fraud-ontology] date_rules[%r] names column(s) %s which "
                    "do not exist in the dataset — dropped; fix sources.json",
                    name, missing)
                continue
        mind = r.get("min_days_between")
        maxd = r.get("max_days_between")
        out.append({
            "name": name, "earlier_field": e_f, "later_field": l_f,
            "min_days_between": int(mind) if isinstance(mind, (int, float)) else 0,
            **({"max_days_between": int(maxd)}
               if isinstance(maxd, (int, float)) and maxd >= 0 else {}),
        })
    return out or None


def verify_against_from_screening(
    fraud_screening: Any,
    by_ref: Optional[Dict[str, Dict[str, Any]]] = None,
    dataset_columns: Optional[List[Dict[str, Any]]] = None,
    domain: Any = None,
) -> Optional[List[Dict[str, Any]]]:
    """The generic cross-dataset verification configs (plan F4) the ontology
    declares, with target routing resolved from the catalogue. None when
    undeclared. Same fail-loud discipline as payment_proof: an unknown target
    dataset, a bad match_field or an unpinned doc_column drops THAT block
    loudly; a bad compare target_field drops just that comparison. Tolerances
    resolve the SAME way as payment_proof: ontology-explicit > vertical pack
    (``domain``) > platform default — the claims pack's 7-day window must
    reach a claims-vertical verify date compare too."""
    if isinstance(fraud_screening, dict):
        blocks = fraud_screening.get("verify_against")
    elif fraud_screening is not None:
        blocks = getattr(fraud_screening, "verify_against", None)
    else:
        return None
    if not blocks:
        return None

    col_names = {
        (c.get("name") or c.get("physical_name"))
        for c in (dataset_columns or [])
        if c.get("artifact_role") and (c.get("name") or c.get("physical_name"))
    }
    out: List[Dict[str, Any]] = []
    for raw in blocks:
        b = raw if isinstance(raw, dict) else {
            k: getattr(raw, k) for k in (
                "name", "target_dataset", "match_field", "doc_column",
                "doc_ref_field", "compare", "description") if hasattr(raw, k)}
        name = b.get("name") or "verify"
        target_ref = b.get("target_dataset")
        match_field = b.get("match_field")
        doc_column = b.get("doc_column")
        if not (target_ref and match_field and doc_column):
            logger.warning(
                "[fraud-ontology] verify_against[%r] needs target_dataset + "
                "match_field + doc_column — dropped; fix sources.json", name)
            continue
        if doc_column not in col_names:
            logger.warning(
                "[fraud-ontology] verify_against[%r].doc_column %r is not a "
                "role-tagged artifact column of this dataset — dropped; the "
                "check must be PINNED to one screened document", name, doc_column)
            continue
        entry = (by_ref or {}).get(target_ref)
        if entry is None:
            logger.warning(
                "[fraud-ontology] verify_against[%r].target_dataset %r is not "
                "in the catalogue — dropped; fix sources.json (or crawl the "
                "source)", name, target_ref)
            continue
        target_cols = _column_names(entry)
        if match_field not in target_cols:
            logger.warning(
                "[fraud-ontology] verify_against[%r].match_field %r does not "
                "exist in %r — dropped; fix sources.json",
                name, match_field, target_ref)
            continue
        compare: List[Dict[str, Any]] = []
        for c in (b.get("compare") or []):
            c = c if isinstance(c, dict) else {
                k: getattr(c, k) for k in (
                    "doc_field", "target_field", "type", "tolerance_pct",
                    "window_days") if hasattr(c, k)}
            if c.get("target_field") not in target_cols:
                logger.warning(
                    "[fraud-ontology] verify_against[%r] compares against "
                    "column %r which does not exist in %r — that comparison "
                    "dropped; fix sources.json",
                    name, c.get("target_field"), target_ref)
                continue
            tol = c.get("tolerance_pct")
            win = c.get("window_days")
            _sv = sub_vertical_of(domain)
            if not (isinstance(tol, (int, float)) and tol >= 0):
                packed = pack_default(_sv, "amount_tolerance_pct")
                tol = packed if isinstance(packed, (int, float)) else 1.0
            if not (isinstance(win, int) and win >= 0):
                packed = pack_default(_sv, "date_window_days")
                win = int(packed) if isinstance(packed, (int, float)) else 3
            compare.append({
                "doc_field": c.get("doc_field"),
                "target_field": c.get("target_field"),
                "type": c.get("type") or "text",
                "tolerance_pct": tol,
                "window_days": win,
            })
        cfg: Dict[str, Any] = {
            "name": name,
            "target_source_id": str(target_ref).split(".", 1)[0],
            "target_dataset": target_ref,
            "target_kind": entry.get("kind"),
            "match_field": match_field,
            "doc_column": doc_column,
            "doc_ref_field": b.get("doc_ref_field") or "reference",
            "compare": compare,
        }
        desc = (entry.get("description") or "").strip()
        if desc:
            cfg["target_description"] = desc[:300]
        if b.get("description"):
            cfg["description"] = str(b["description"])[:300]
        out.append(cfg)
    return out or None


def screening_active(
    fraud_screening: Any,
    col_roles: Optional[Dict[str, Dict[str, str]]] = None,
) -> bool:
    """The ONTOLOGY GATE — is fraud screening ON for this dataset?

      * ``applies == True``   → ON.
      * ``applies == False``  → OFF (a hard, explicit opt-out).
      * ``applies`` omitted    → ON iff ≥1 column declares an ``artifact_role``
        (declaring roles is itself the opt-in intent). A fraud_screening block
        carrying only advisory hints (value_fields/identity_fields) leaves
        ``applies`` None and so does NOT read as a hard OFF.

    So fraud detection is a FEATURE THE ONTOLOGY TURNS ON: a source that says
    nothing about fraud gets no fingerprinting/matching at all.
    """
    applies = _applies_value(fraud_screening)
    if applies is not None:
        return bool(applies)
    return any(
        (r or {}).get("artifact_role") in ARTIFACT_ROLES
        for r in (col_roles or {}).values()
    )


def screening_explicit_off(fraud_screening: Any) -> bool:
    """True ONLY when the ontology EXPLICITLY sets ``applies == False`` — a hard
    opt-out. Silence (no block, or a hints-only block) is NOT an opt-out. This is
    what lets autowire distinguish 'the source turned screening OFF' (clear the
    tool) from 'the source said nothing' (leave a hand-authored screen alone)."""
    return _applies_value(fraud_screening) is False


#: column_kind values that denote a MEDIA reference the runtime can resolve.
#: `plain` is the only declared non-media kind; an absent kind is UNKNOWN, not
#: plain (only a handful of live columns declare one), so absent stays eligible.
_MEDIA_COLUMN_KINDS = frozenset({"url", "image_url", "document_url", "file"})


def _fingerprint_targets(col_roles: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """The columns the ontology says to CAPTURE + match: evidence + identity
    artifacts (evidence to catch reuse, identity to verify). ``supporting`` is
    never fingerprinted (its reuse is meaningless).

    A target is auto-wired into the screen's ``url_columns``, so the runtime will
    try to resolve it as media. A column that declares an artifact_role AND an
    explicit non-media ``column_kind`` is a contradiction the author should hear
    about at publish, not as a mid-run resolve failure — so it is excluded and
    reported. Absent column_kind is not a contradiction: it is simply undeclared.
    """
    out: Dict[str, Dict[str, str]] = {}
    for c, r in (col_roles or {}).items():
        if (r or {}).get("artifact_role") not in ("evidence", "identity", "payment_proof"):
            continue
        kind = (r or {}).get("column_kind")
        if kind not in (None, "") and kind not in _MEDIA_COLUMN_KINDS:
            logger.warning(
                "[fraud-ontology] column %r declares artifact_role %r but "
                "column_kind %r (not media: %s) — NOT fingerprinted. An "
                "artifact_role column is auto-wired into url_columns and "
                "resolved as media; a %r column would fail at runtime. Fix "
                "sources.json: drop the role, or declare the real media kind.",
                c, (r or {}).get("artifact_role"), kind,
                sorted(_MEDIA_COLUMN_KINDS), kind,
            )
            continue
        out[c] = r
    return out


def _primary_key(columns: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """The dataset's record-key column (matches the run's record_id)."""
    for c in (columns or []):
        if c.get("is_primary_key"):
            return c.get("name") or c.get("physical_name")
    return None


def _unique_name(base: str, existing: set) -> str:
    name, i = base, 2
    while name in existing:
        name, i = f"{base}_{i}", i + 1
    return name


# ── Vertical packs (Phase 2) ─────────────────────────────────────────────────
# Per-sub-vertical DEFAULTS applied only where the ontology is silent — an
# explicit sources.json value always wins. Data, not code: a pack tunes
# tolerances and advises on missing annotations; it never forks behavior and
# never turns screening on (fraud_screening stays the only on-switch).
VERTICAL_PACK_DEFAULTS: Dict[str, Dict[str, Any]] = {
    # A meter/premise inspection is bound to ONE address — 10 km would let a
    # photo from the next town pass. 1 km still absorbs GPS drift.
    "metering_inspection": {"gps_radius_km": 1.0},
    "equipment_inspection": {"gps_radius_km": 1.0},
    # Insurance repair payments settle slower than utility/collections ledgers.
    "claims": {"date_window_days": 7},
}

# Sub-verticals whose KILLER check is E4 — a dataset there with document
# artifacts but no payment_proof declaration gets a publish-time advisory.
_PAYMENT_PROOF_VERTICALS = frozenset({"loan_recovery", "power_recovery"})
# Sub-verticals whose killer check is E1 photo-vs-claim.
_CLAIM_CONTEXT_VERTICALS = frozenset(
    {"claims", "metering_inspection", "equipment_inspection"})


def sub_vertical_of(domain: Any) -> Optional[str]:
    """The dataset's sub-vertical from its ontology domain triple, or None."""
    if domain is None:
        return None
    sv = (domain.get("sub_vertical") if isinstance(domain, dict)
          else getattr(domain, "sub_vertical", None))
    return str(sv) if sv else None


def pack_default(sub_vertical: Optional[str], key: str) -> Optional[Any]:
    """The vertical pack's default for ``key`` — None when no pack speaks."""
    if not sub_vertical:
        return None
    return VERTICAL_PACK_DEFAULTS.get(sub_vertical, {}).get(key)


def advise_missing_annotations(
    tool_name: str, ref: str, entry: Dict[str, Any],
    claim_cfg: Optional[Dict[str, Any]], pp_cfg: Optional[Dict[str, Any]],
) -> None:
    """Phase 2 — the learning-curve-visible wedge applied to onboarding: when a
    dataset's declared sub-vertical implies a killer check that its ontology
    hasn't armed, SAY SO at publish (WARN — advisory, never an error; `domain`
    tunes and advises, `fraud_screening` alone switches things on)."""
    sv = sub_vertical_of(entry.get("domain"))
    if not sv:
        return
    if sv in _PAYMENT_PROOF_VERTICALS and pp_cfg is None:
        has_docs = any(c.get("artifact_role") or c.get("column_kind") in
                       ("document_url", "url", "file")
                       for c in (entry.get("columns") or []))
        if has_docs:
            logger.warning(
                "[fraud-advisory] %r: dataset %r declares sub_vertical %r and "
                "carries document artifacts, but no fraud_screening."
                "payment_proof — the killer check for this vertical "
                "(\"I already paid — here's the receipt\") is OFF. Declare the "
                "ledger + tag the receipt column artifact_role='payment_proof' "
                "(sources-file.md §10.2).", tool_name, ref, sv)
    if sv in _CLAIM_CONTEXT_VERTICALS and claim_cfg is None:
        logger.warning(
            "[fraud-advisory] %r: dataset %r declares sub_vertical %r but no "
            "claim-context fields (incident_date_field / location_lat+lon) — "
            "the photo-vs-claim checks for this vertical are OFF. Annotate "
            "them in sources.json (sources-file.md §10.2).", tool_name, ref, sv)


def locale_from_domain(domain: Any) -> Optional[str]:
    """The fraud locale pack key from a dataset's ontology domain triple —
    lowercase ISO country ('IN' → 'in'). None when the ontology declares no
    domain: the deployment env (FRAUD_LOCALE) stays the decider, exactly the
    pre-ontology behavior. The ONTOLOGY WINS over the env when present."""
    if domain is None:
        return None
    country = (domain.get("country") if isinstance(domain, dict)
               else getattr(domain, "country", None))
    if not country:
        return None
    return str(country).strip().lower() or None


def domain_badge(domain: Any) -> Optional[Dict[str, str]]:
    """The (vertical, sub_vertical, country) triple as the flat badge dict
    stamped on screens for the admin UI. None when the ontology is silent."""
    if not isinstance(domain, dict):
        return None
    out = {k: str(domain[k]) for k in ("vertical", "sub_vertical", "country")
           if domain.get(k)}
    return out or None


# The description suffix always starts with this marker so it can be refreshed
# idempotently on reconcile (strip the old sentence, append the current one)
# without touching whatever precedes it — including a hand-authored description.
_PAYMENT_SENTENCE_MARKER = " Payment-proof verification:"


def _payment_proof_sentence(payment_proof: Optional[Dict[str, Any]]) -> str:
    """F2 — the agent must know WHICH ledger verifies payment references and
    WHICH attached document they may come from, so a record carrying several
    bills (receipt + purchase invoice) is matched correctly. Empty when the
    check is off."""
    if not payment_proof:
        return ""
    led = payment_proof.get("ledger_dataset")
    led_desc = payment_proof.get("ledger_description")
    docs = payment_proof.get("doc_columns") or []
    return (
        f"{_PAYMENT_SENTENCE_MARKER} references are checked against the "
        f"ledger '{led}'"
        + (f" ({led_desc})" if led_desc else "")
        + (f"; supply the extracted payment fields ONLY from the "
           f"payment-proof document in column(s) {', '.join(docs)} — "
           f"never from other attached bills." if docs else ".")
    )


_VERIFY_SENTENCE_MARKER = " Cross-dataset verification:"


def _verify_sentence(configs: Optional[List[Dict[str, Any]]]) -> str:
    """The agent-facing sentence naming each verify_against check: WHICH
    document verifies against WHICH dataset, and what that dataset is for."""
    if not configs:
        return ""
    parts = []
    for c in configs:
        parts.append(
            f"'{c.get('name')}' verifies the document in column "
            f"{c.get('doc_column')} against {c.get('target_dataset')}"
            + (f" ({c['target_description']})" if c.get("target_description") else "")
            + (f" — {c['description']}" if c.get("description") else "")
        )
    return (f"{_VERIFY_SENTENCE_MARKER} " + "; ".join(parts)
            + ". Supply each check's extracted fields ONLY from its named document.")


# Hard ceiling for the COMPOSED description. Matches (with a safety margin)
# ConsistencyCheckTool.description's max_length=1600 in models.py: the
# reconcile path writes via setattr (no validate_assignment), so an oversize
# value would persist unvalidated and BRICK the app on its next
# AgentSpec.model_validate load — clamp here, at the single compose point.
_DESC_HARD_MAX = 1580


def _refresh_check_sentences(
    description: Optional[str],
    payment_proof: Optional[Dict[str, Any]],
    verify_against: Optional[List[Dict[str, Any]]],
) -> str:
    """Both autowired sentences brought current in one idempotent pass — only
    the marker suffixes are touched; a hand-authored prefix survives. The
    result is clamped to the model's description cap (see _DESC_HARD_MAX)."""
    base = description or ""
    for marker in (_PAYMENT_SENTENCE_MARKER, _VERIFY_SENTENCE_MARKER):
        base = base.split(marker, 1)[0]
    out = (base.rstrip() + _payment_proof_sentence(payment_proof)
           + _verify_sentence(verify_against))
    if len(out) > _DESC_HARD_MAX:
        logger.warning(
            "[fraud-autowire] composed tool description is %d chars — clamped "
            "to %d. Shorten the ledger/target descriptions in sources.json to "
            "keep the full agent guidance.", len(out), _DESC_HARD_MAX)
        out = out[: _DESC_HARD_MAX - 1].rstrip() + "…"
    return out


def _make_consistency_tool(
    *, name: str, data_source_id: str, key_field: str,
    url_columns: List[str], url_column_roles: Dict[str, Dict[str, str]],
    claim_context: Optional[Dict[str, Any]] = None,
    identity_fields: Optional[List[str]] = None,
    payment_proof: Optional[Dict[str, Any]] = None,
    locale: Optional[str] = None,
    domain: Optional[Dict[str, str]] = None,
    verify_against: Optional[List[Dict[str, Any]]] = None,
    date_rules: Optional[List[Dict[str, Any]]] = None,
    dataset_kind: Optional[str] = None,
) -> Any:
    """Build a record-bound consistency_check tool. Prefer the typed model
    (validates against extra='forbid'); fall back to a plain dict when models
    isn't importable (tests)."""
    # Composed through the SAME clamped refresher the reconcile path uses, so
    # the create path can never produce a description the model would reject.
    description = _refresh_check_sentences(
        "Auto-wired fraud screen (ontology-driven): fingerprints this record's "
        "artifacts (SHA-256 / dHash / embedding) against prior records and flags "
        "reused EVIDENCE, while treating an IDENTITY match as verification. See "
        "the dataset's sources.json fraud_screening.",
        payment_proof, verify_against,
    )
    payload = {
        "kind": "consistency_check",
        "name": name,
        "description": description,
        "data_source_id": data_source_id,
        "key_field": key_field,
        "url_columns": list(url_columns),
        "url_column_roles": dict(url_column_roles),
        "claim_context": dict(claim_context) if claim_context else None,
        "payment_proof": dict(payment_proof) if payment_proof else None,
        # Locale pack key from the ontology's domain.country — decides which ID
        # validators run and how ambiguous dates parse for THIS dataset's checks.
        "locale": locale,
        "domain": dict(domain) if domain else None,
        "verify_against": [dict(v) for v in verify_against] if verify_against else None,
        "date_rules": [dict(d) for d in date_rules] if date_rules else None,
        "dataset_kind": dataset_kind if date_rules else None,
        # Stamped at CREATE time too — the reconcile loop only reaches tools
        # that already exist, so omitting these here would leave a first-publish
        # screen without the ontology's linkable keys until a republish.
        "identity_fields": sorted(identity_fields or []),
    }
    try:
        from models import ConsistencyCheckTool
    except ModuleNotFoundError as exc:
        # ONLY the model-less unit-test context (the service package's own
        # ``models`` module isn't on the path) falls back to a plain dict. A
        # ModuleNotFoundError for anything ELSE — a real transitive dependency
        # missing inside models.py on a deploy box — must propagate (fail loud),
        # as must a ValidationError from a malformed payload.
        if (exc.name or "").split(".")[0] != "models":
            raise
        return payload
    return ConsistencyCheckTool(**payload)


def _make_fraud_synthesis_tool(*, name: str) -> Any:
    """Build the T3 gated synthesis tool with model defaults. Prefer the typed
    model (validates against extra='forbid'); fall back to a plain dict when
    models isn't importable (tests) — same pattern as _make_consistency_tool."""
    payload = {
        "kind": "fraud_synthesis",
        "name": name,
        "description": (
            "Auto-wired T3 fraud cross-examination (ontology-driven; added "
            "because a dataset opted into fraud screening). Call LAST, after "
            "the fraud screen and all analyses, with record_id + case context "
            "+ ALL collected screening signals (the consistency_check output "
            "and every finding's artifact_flags). Gates server-side and "
            "persists the screening for the admin panel + L3 calibration; "
            "output is officer EVIDENCE, never a verdict."
        ),
    }
    try:
        from models import FraudSynthesisTool
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] != "models":
            raise
        return payload
    return FraudSynthesisTool(**payload)


def fraud_active_for_agent(agent_spec: Any) -> bool:
    """True iff the ontology enabled fraud screening for this app — i.e. the
    autowire wired at least one ``consistency_check`` tool with ``url_columns``
    AND a ``data_source_id`` binding.

    APP-scoped on purpose: this gates the app-LEVEL fraud surfaces (the
    ``fraud_enabled`` flag, L3 calibration, L2 case-learning, the fraud review UI)
    — "does this app have ANY fraud screening at all". Nothing about fraud runs or
    renders unless ``sources.json`` turned it on. Call AFTER ``autowire_fraud_roles``
    so it reflects the ontology, not just what the LLM happened to author.

    ``data_source_id`` is required so a WIRED screen means the same thing here as
    at runtime (``tools_v2_dispatch._dataset_fraud_active`` + the FRAUD SCREEN
    prompt block): an unbound consistency_check can't resolve artifacts, so the
    runtime treats it as inactive — if this predicate said True for it, the app card
    would offer "Calibrate fraud" for surfaces that never fire.

    NB the runtime's PER-ITEM gate is per-DATASET, not app-scoped: the two ask
    different questions (any-screen-in-the-app vs is-THIS-dataset-screened) off the
    same wired-screen definition. An app can be fraud_enabled while an unscreened
    dataset inside it is never fingerprinted."""
    for t in (getattr(agent_spec, "tools_v2", None) or []):
        if (
            _tool_attr(t, "kind") == "consistency_check"
            and _tool_attr(t, "url_columns")
            and _tool_attr(t, "data_source_id")
        ):
            return True
    return False


def autowire_fraud_roles(
    agent_spec: Any, catalogue: Any, data_sources: Any = None
) -> int:
    """Builder auto-wiring — the ONTOLOGY, not the LLM, decides fraud SCOPE: which
    datasets are screened, which artifact columns are captured, how a reuse hit is
    read, and which identifiers link cases. (SENSITIVITY — the escalation gate,
    field_types, signal weights — is NOT ontology-driven; see the module docstring.)

    For each ``consistency_check`` tool, resolve its dataset
    (``data_source_id`` → ``data_sources[id].ref`` → catalogue entry) and let
    that dataset's ontology decide:

      * screening OFF for the dataset  → CLEAR ``url_columns`` (no capture, no match).
      * screening ON                    → set ``url_columns`` to the dataset's
        evidence/identity artifact columns and stamp their roles into
        ``url_column_roles``.

    So both WHICH artifacts are captured and HOW a match is read come from
    ``sources.json`` — the LLM never decides fraud scope. Deterministic +
    idempotent.

    The consistency_check tools live on ``agent_spec.tools_v2``, but the
    ``data_sources`` (id → dataset ref) live on the *AppSpec*, not the AgentSpec —
    so the caller MUST pass them in. ``catalogue`` is the validator's
    ``{(source_id, dataset_id): entry_dict}`` map. Returns the number of tools changed.
    """
    # dataset_id (ref) → catalogue entry
    by_ref: Dict[str, Dict[str, Any]] = {}
    for (_src, ds), entry in (catalogue or {}).items():
        if entry:
            by_ref[ds] = entry
    # app data_source id → catalogue dataset_id (only mcp sources bind to a
    # dataset). data_sources live on the AppSpec and MUST be passed in — AgentSpec
    # has no such field, so there is deliberately no getattr(agent_spec, …) fallback
    # (it would be permanently None in prod and mask a missing wire).
    ds_ref: Dict[str, str] = {}
    for ds in (data_sources or []):
        if _tool_attr(ds, "type") == "mcp":
            _id, _ref = _tool_attr(ds, "id"), _tool_attr(ds, "ref")
            if _id and _ref:
                ds_ref[_id] = _ref

    changed_count = 0
    for tool in (getattr(agent_spec, "tools_v2", None) or []):
        if _tool_attr(tool, "kind") != "consistency_check":
            continue
        name = _tool_attr(tool, "name") or "consistency_check"
        ds_id = _tool_attr(tool, "data_source_id") or ""
        ref = ds_ref.get(ds_id)
        entry = by_ref.get(ref) if ref else None

        cur_cols = list(_tool_attr(tool, "url_columns") or [])
        cur_roles = dict(_tool_attr(tool, "url_column_roles") or {})

        if entry is None:
            # Dataset unresolved: non-mcp source, a catalogue outage, or ref
            # drift. Do NOT clear an existing screen on a transient miss — that
            # would silently disable fraud detection on an infra blip. Leave the
            # tool exactly as authored and make the gap visible.
            if cur_cols or cur_roles:
                logger.warning(
                    "[fraud-autowire] %r: dataset for data_source_id=%r not in "
                    "catalogue — leaving existing url_columns untouched", name, ds_id)
            continue

        col_roles = roles_from_catalogue_columns(entry.get("columns"))
        fraud_screening = entry.get("fraud_screening")
        if not screening_active(fraud_screening, col_roles):
            # Screening is OFF for this dataset. Distinguish an EXPLICIT opt-out
            # (applies=false) — which should clear the screen — from mere SILENCE
            # (source says nothing about fraud). On silence we must NOT wipe a
            # hand-authored screen: apps configured artifact fingerprinting before
            # the ontology existed, and clearing it would silently disable them.
            if screening_explicit_off(fraud_screening):
                if (cur_cols or cur_roles or _tool_attr(tool, "claim_context")
                        or _tool_attr(tool, "payment_proof")
                        or _tool_attr(tool, "verify_against")):
                    logger.info(
                        "[fraud-autowire] %r: ontology explicitly opts dataset %r "
                        "OUT (applies=false) — clearing url_columns", name, ref)
                    _tool_set(tool, "url_columns", [])
                    _tool_set(tool, "url_column_roles", {})
                    _tool_set(tool, "claim_context", None)
                    _tool_set(tool, "payment_proof", None)
                    _tool_set(tool, "verify_against", None)
                    _tool_set(tool, "date_rules", None)
                    changed_count += 1
            elif cur_cols or cur_roles:
                logger.info(
                    "[fraud-autowire] %r: dataset %r declares no fraud ontology — "
                    "leaving hand-authored url_columns untouched", name, ref)
            continue

        targets = _fingerprint_targets(col_roles)
        if not targets:
            # applies=true (or roles declared) but no evidence/identity artifact
            # column to fingerprint — screening is on with nothing to capture.
            logger.warning(
                "[fraud-autowire] %r: dataset %r opts INTO screening but declares "
                "no evidence/identity/payment_proof artifact column — nothing to "
                "fingerprint", name, ref)
        # Reconcile the tool's columns with the ontology:
        #   • capture set = the ontology's evidence/identity columns (`targets`);
        #   • a column the ontology KNOWS about (in `col_roles`, incl. supporting)
        #     but did NOT put in targets is a deliberate OPT-OUT (downgraded to
        #     supporting) → DROP it, even if a prior wire had it;
        #   • a column the ontology says NOTHING about is a human addition → KEEP.
        ontology_known = set(col_roles)                       # every annotated column
        human_extra = [c for c in cur_cols if c not in ontology_known]
        new_cols = sorted(set(targets) | set(human_extra))
        new_roles = {c: cur_roles[c] for c in human_extra if c in cur_roles}
        new_roles.update(targets)                             # ontology role wins for its columns
        if new_cols != sorted(cur_cols) or new_roles != cur_roles:
            _tool_set(tool, "url_columns", new_cols)
            _tool_set(tool, "url_column_roles", new_roles)
            changed_count += 1

        # Cross-record linkable keys — the ontology's OTHER fraud declaration. The
        # entity index answers "this identifier is on another case"; WHICH columns
        # count as identifiers must come from the source (which knows its keys), not
        # from the builder's field_types pin or a field-name heuristic. Additive at
        # read time (see entity_links.extract_linkable), so stamping can only widen
        # linking, never disable it.
        new_idents = sorted(_identity_fields(fraud_screening))
        if new_idents and sorted(_tool_attr(tool, "identity_fields") or []) != new_idents:
            _tool_set(tool, "identity_fields", new_idents)
            changed_count += 1

        # EXIF↔claim comparator config (E1) — which record columns carry the
        # claimed incident date / site coordinates. Declared in sources.json,
        # stamped here with the dataset kind the runtime's structured
        # read-by-key needs. None ⇒ the comparator never runs.
        new_claim = claim_context_from_screening(
            fraud_screening, entry.get("kind"), _column_names(entry),
            domain=entry.get("domain"),
        )
        cur_claim = _tool_attr(tool, "claim_context")
        if hasattr(cur_claim, "model_dump"):
            # Typed ClaimContext on a parsed spec — normalize so an unchanged
            # config doesn't read as a diff (and churn changed_count) every
            # publish just because instance != dict.
            cur_claim = cur_claim.model_dump(exclude_none=True)
        if (cur_claim or None) != new_claim:
            _tool_set(tool, "claim_context", new_claim)
            changed_count += 1

        # Payment-proof config (E4) — which ledger dataset verifies this
        # dataset's extracted receipt references. Same normalize-and-diff
        # pattern as claim_context.
        new_pp = payment_proof_from_screening(
            fraud_screening, by_ref, dataset_columns=entry.get("columns"),
            domain=entry.get("domain"))
        cur_pp = _tool_attr(tool, "payment_proof")
        if hasattr(cur_pp, "model_dump"):
            cur_pp = cur_pp.model_dump(exclude_none=True)
        if (cur_pp or None) != new_pp:
            _tool_set(tool, "payment_proof", new_pp)
            changed_count += 1

        # E6 declarative date rules — same normalize-and-diff pattern; the
        # dataset kind rides along for the server-side record read.
        new_dr = date_rules_from_screening(fraud_screening, _column_names(entry))
        cur_dr = _tool_attr(tool, "date_rules")
        if cur_dr:
            cur_dr = [d.model_dump(exclude_none=True) if hasattr(d, "model_dump")
                      else d for d in cur_dr]
        if (cur_dr or None) != new_dr:
            _tool_set(tool, "date_rules", new_dr)
            changed_count += 1
        new_kind = entry.get("kind") if new_dr else None
        if (_tool_attr(tool, "dataset_kind") or None) != new_kind:
            _tool_set(tool, "dataset_kind", new_kind)
            changed_count += 1

        # Generic cross-dataset verifications (plan F4) — same pattern.
        new_va = verify_against_from_screening(
            fraud_screening, by_ref, dataset_columns=entry.get("columns"),
            domain=entry.get("domain"))
        cur_va = _tool_attr(tool, "verify_against")
        if cur_va and not isinstance(cur_va, list):
            cur_va = list(cur_va)
        if cur_va:
            cur_va = [v.model_dump(exclude_none=True) if hasattr(v, "model_dump")
                      else v for v in cur_va]
        if (cur_va or None) != new_va:
            _tool_set(tool, "verify_against", new_va)
            changed_count += 1

        # Keep the description's autowired sentences current (F2): the agent
        # reads WHICH ledger/dataset + WHICH document column from here.
        # Idempotent — only the marker suffixes are touched, a hand-authored
        # description survives.
        _cur_desc = _tool_attr(tool, "description")
        _new_desc = _refresh_check_sentences(_cur_desc, new_pp, new_va)
        if _new_desc != (_cur_desc or ""):
            _tool_set(tool, "description", _new_desc)
            changed_count += 1

        # Locale pack key from the ontology's domain triple (Phase 1) — the
        # ONTOLOGY wins over the FRAUD_LOCALE env for this dataset's checks.
        # None clears an earlier stamp when the ontology dropped its domain
        # (fall back to env), never leaves a stale country in force.
        new_locale = locale_from_domain(entry.get("domain"))
        if (_tool_attr(tool, "locale") or None) != new_locale:
            logger.info(
                "[fraud-autowire] %r: dataset %r locale set to %r (ontology domain)",
                name, ref, new_locale)
            _tool_set(tool, "locale", new_locale)
            changed_count += 1
        new_badge = domain_badge(entry.get("domain"))
        if (_tool_attr(tool, "domain") or None) != new_badge:
            _tool_set(tool, "domain", new_badge)
            changed_count += 1

        # Phase 2 — sub-vertical advisories: a declared line of business whose
        # killer check isn't armed gets a publish-time nudge (WARN only).
        advise_missing_annotations(name, ref, entry, new_claim, new_pp)

    # Deterministic auto-CREATE — a screened dataset with no consistency_check
    # tool gets one, so fraud detection needs ZERO builder-LLM judgment. The
    # ontology alone is the trigger: applies=true (or roles declared) + a primary
    # key + ≥1 evidence/identity artifact column ⇒ a record-bound screen is wired.
    tools_list = getattr(agent_spec, "tools_v2", None)
    if isinstance(tools_list, list):
        bound = {
            _tool_attr(t, "data_source_id")
            for t in tools_list if _tool_attr(t, "kind") == "consistency_check"
        }
        names = {_tool_attr(t, "name") for t in tools_list}
        for ds in (data_sources or []):
            if _tool_attr(ds, "type") != "mcp":
                continue
            ds_id = _tool_attr(ds, "id")
            if not ds_id or ds_id in bound:
                continue
            entry = by_ref.get(_tool_attr(ds, "ref") or "")
            if not entry:
                continue
            col_roles = roles_from_catalogue_columns(entry.get("columns"))
            if not screening_active(entry.get("fraud_screening"), col_roles):
                continue
            targets = _fingerprint_targets(col_roles)
            pk = _primary_key(entry.get("columns"))
            if not targets or not pk:
                # Screening is ON for this dataset but it can't be auto-wired —
                # surface WHY (missing artifact column, or no primary key to bind
                # the screen to a record) rather than silently skipping.
                logger.warning(
                    "[fraud-autowire] dataset %r opts into screening but cannot "
                    "auto-create a screen: %s%s",
                    _tool_attr(ds, "ref"),
                    "no evidence/identity artifact column" if not targets else "",
                    "; no primary key" if not pk else "")
                continue  # nothing to fingerprint, or no record key to bind
            _new_name = _unique_name(f"fraud_screen_{ds_id}", names)
            _claim_cfg = claim_context_from_screening(
                entry.get("fraud_screening"), entry.get("kind"),
                _column_names(entry), domain=entry.get("domain"),
            )
            _pp_cfg = payment_proof_from_screening(
                entry.get("fraud_screening"), by_ref,
                dataset_columns=entry.get("columns"),
                domain=entry.get("domain"))
            tool = _make_consistency_tool(
                name=_new_name,
                data_source_id=ds_id, key_field=pk,
                url_columns=sorted(targets.keys()), url_column_roles=targets,
                claim_context=_claim_cfg,
                identity_fields=_identity_fields(entry.get("fraud_screening")),
                payment_proof=_pp_cfg,
                locale=locale_from_domain(entry.get("domain")),
                domain=domain_badge(entry.get("domain")),
                verify_against=verify_against_from_screening(
                    entry.get("fraud_screening"), by_ref,
                    dataset_columns=entry.get("columns"),
                    domain=entry.get("domain")),
                date_rules=date_rules_from_screening(
                    entry.get("fraud_screening"), _column_names(entry)),
                dataset_kind=entry.get("kind"),
            )
            # Sub-vertical advisories fire on FIRST publish too — the create
            # path is exactly where a new integrator hears them.
            advise_missing_annotations(
                _new_name, ds_ref.get(ds_id) or ds_id, entry, _claim_cfg, _pp_cfg)
            tools_list.append(tool)
            names.add(_tool_attr(tool, "name"))
            bound.add(ds_id)
            changed_count += 1

    # Stamp the ontology role onto the per-image tools (image_analyze / doc_extract)
    # from the SAME sources.json — so each is role-aware on ITS OWN, independent of
    # whether a consistency_check happens to cover the column. This closes the
    # residual false-identification: an `identity` artifact on a dataset with no
    # primary key (so no auto-created screen) still reads a reuse hit as verification,
    # not fraud. The role is a property of the artifact column, so it is stamped
    # whenever the column is annotated — regardless of screening on/off (fingerprinting
    # itself is still gated elsewhere). An un-annotated column is left unset ⇒ the
    # safe evidence/suspicious default at read time.
    for tool in (getattr(agent_spec, "tools_v2", None) or []):
        if _tool_attr(tool, "kind") not in ("image_analyze", "doc_extract"):
            continue
        col = _tool_attr(tool, "url_column")
        ds_id = _tool_attr(tool, "data_source_id")
        if not (col and ds_id):
            continue
        entry = by_ref.get(ds_ref.get(ds_id) or "")
        if not entry:
            continue
        role = roles_from_catalogue_columns(entry.get("columns")).get(col)
        if not role:
            continue  # ontology says nothing about this column → leave default
        new_role = role.get("artifact_role")
        new_policy = role.get("reuse_policy")
        if (_tool_attr(tool, "artifact_role") != new_role
                or _tool_attr(tool, "reuse_policy") != new_policy):
            _tool_set(tool, "artifact_role", new_role)
            _tool_set(tool, "reuse_policy", new_policy)
            changed_count += 1

    # T3 synthesis auto-wiring — a screened app whose agent lacks the
    # fraud_synthesis tool produces evidence the officer sees but NEVER
    # persists a screening row, so the Screening Health panel shows zeros for
    # it (found live on prod: inspection-triage caught fraud, counted nothing,
    # until the tool was hand-appended). When the ontology turned screening ON
    # (≥1 wired screen), make sure the synthesis tool exists. ADDITIVE ONLY:
    # never removed on opt-out (it no-ops without signals, and removing could
    # wipe a builder-tuned gate_min_points/sample_rate) and an existing tool —
    # hand-authored or previously wired — is left untouched.
    tools_list = getattr(agent_spec, "tools_v2", None)
    if (
        isinstance(tools_list, list)
        and fraud_active_for_agent(agent_spec)
        and not any(_tool_attr(t, "kind") == "fraud_synthesis" for t in tools_list)
    ):
        names = {_tool_attr(t, "name") for t in tools_list}
        tools_list.append(
            _make_fraud_synthesis_tool(name=_unique_name("fraud_synthesis", names))
        )
        changed_count += 1
        logger.info(
            "[fraud-autowire] screening is ON but the agent had no "
            "fraud_synthesis tool — auto-wired one (screenings now persist "
            "for the Screening Health panel)")

    return changed_count


def roles_from_catalogue_columns(
    columns: Optional[List[Dict[str, Any]]],
) -> Dict[str, Dict[str, str]]:
    """Extract ``{column_name: {artifact_role, reuse_policy}}`` from a catalogue /
    ontology column list — only for columns that actually declare a role, so the
    map stays sparse (absent ⇒ safe default at read time)."""
    out: Dict[str, Dict[str, str]] = {}
    for c in (columns or []):
        name = c.get("name") or c.get("physical_name")
        role = c.get("artifact_role")
        policy = c.get("reuse_policy")
        if not name:
            continue
        # Fail loud on a mis-annotation: a non-empty value that isn't a valid
        # role/policy is almost always a sources.json typo (e.g. 'evidance'). If
        # we silently dropped it, a column meant to OPT IN would opt OUT with no
        # signal. Surface it; the value is still ignored (safe default applies).
        if role not in (None, "") and role not in ARTIFACT_ROLES:
            logger.warning(
                "[fraud-ontology] column %r declares invalid artifact_role %r "
                "(allowed: %s) — ignored; check sources.json",
                name, role, sorted(ARTIFACT_ROLES))
        if policy not in (None, "") and policy not in REUSE_POLICIES:
            logger.warning(
                "[fraud-ontology] column %r declares invalid reuse_policy %r "
                "(allowed: %s) — ignored; check sources.json",
                name, policy, sorted(REUSE_POLICIES))
        entry: Dict[str, str] = {}
        if role in ARTIFACT_ROLES:
            entry["artifact_role"] = role
        if policy in REUSE_POLICIES:
            entry["reuse_policy"] = policy
        # Carried so _fingerprint_targets can refuse to auto-wire a NON-media
        # column into url_columns (the runtime would try to presign a plain text
        # value and fail mid-run). Only an EXPLICIT kind counts — absent means
        # "not declared", which is the common case and must keep working.
        kind = c.get("column_kind")
        if entry and kind not in (None, ""):
            entry["column_kind"] = str(kind)
        if entry:
            out[name] = entry
    return out
