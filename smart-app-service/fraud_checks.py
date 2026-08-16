"""Fraud & consistency primitives — Phase P1 of docs/fraud-detection-primitives-plan.md.

Deterministic, ZERO-LLM building blocks (the T0 tier of the funnel):

  * Normalizers + field-type detection (phone / amount / date / id / name / text)
  * Format & checksum validators (PAN, IFSC, GSTIN, VIN check-digit, Aadhaar
    Verhoeff, email, Indian phone)
  * ``cross_check`` — record-claimed values vs artifact-extracted values →
    structured ``mismatches[]``
  * ``arithmetic_check`` — invoice line items vs stated total
  * Artifact fingerprinting — SHA-256 exact-duplicate detection across cases
    (env-routed ``smartapp_artifact_fingerprints`` collection) + EXIF / PDF
    metadata extraction with conservative anomaly flags

Design rules (see the plan): pointers-not-payload, explainable-only signals
(every finding carries WHY), no fuzzy matching in v1, and screening output is
EVIDENCE on a recommendation — never an auto-reject.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_FINGERPRINT_COLLECTION = "smartapp_artifact_fingerprints"


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def normalize_phone(v: str) -> str:
    """Digits only; compare on the LAST 10 (Indian numbers with/without +91/0)."""
    digits = re.sub(r"\D", "", str(v or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def _default_locale() -> str:
    """Deployment locale for validators + date-order (FRAUD_LOCALE env via
    settings; 'us' default — primary market). Single-tenant deployments make
    a deployment-level locale the right grain; per-field overrides stay
    available through the tool's ``field_types``."""
    try:
        from config import get_settings

        return (get_settings().fraud_locale or "us").lower()
    except Exception as exc:  # noqa: BLE001 — config unavailable (bare tests)
        # Fail LOUD before falling back: a silent locale flip changes date
        # parsing + validator packs and corrupts mismatch verdicts.
        log.warning("[FRAUD] locale resolution failed (%s) — falling back to 'us'", exc)
        return "us"


def normalize_amount(v: Any) -> Optional[Decimal]:
    """'$123,456.00' / '₹1,23,456' / 123456.0 → Decimal. Currency symbols and
    digit-grouping commas (both US 123,456 and Indian 1,23,456) stripped."""
    if v is None:
        return None
    s = re.sub(r"[₹$€£,\s]|INR|USD|EUR|GBP|Rs\.?", "", str(v), flags=re.IGNORECASE)
    try:
        return Decimal(s) if s else None
    except InvalidOperation:
        return None


# Unambiguous formats (safe everywhere) vs slash-ambiguous ones whose ORDER is
# locale-dependent: 03/04/2026 is Mar 4 in the US, 3 Apr in India. The locale
# decides which interpretation is tried first — a silent wrong-date cross-check
# is worse than none.
_DATE_COMMON = ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y")
_DATE_DMY = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y")
_DATE_MDY = ("%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y")


def normalize_date(v: Any, locale: Optional[str] = None) -> Optional[str]:
    """Best-effort parse → 'YYYY-MM-DD'. ISO first, then locale-ordered."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    # ISO with time / tz tail (e.g. 2026-06-14T10:30:00+05:30)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})([T ]|$)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    loc = (locale or _default_locale())
    ordered = _DATE_COMMON + ((_DATE_MDY + _DATE_DMY) if loc == "us" else (_DATE_DMY + _DATE_MDY))
    for fmt in ordered:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalize_id(v: str) -> str:
    """Uppercase, strip spaces/dashes — SSN/VIN/PAN/policy-no comparisons."""
    return re.sub(r"[\s\-]", "", str(v or "")).upper()


def normalize_name(v: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation, honorifics & suffixes."""
    s = re.sub(r"[^\w\s]", "", str(v or "").lower())
    s = re.sub(r"\b(mr|mrs|ms|dr|prof|shri|smt|kum)\b\.?", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def normalize_text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


# ---------------------------------------------------------------------------
# Field-type detection (by field NAME — closed vocabulary, explainable)
# ---------------------------------------------------------------------------

_TYPE_PATTERNS: List[Tuple[str, str]] = [
    (r"phone|mobile|contact_no|msisdn|cell", "phone"),
    (r"amount|total|premium|value|cost|price|sum_insured|claim_amt|deductible|payout", "amount"),
    (r"date|dob|_on$|_at$", "date"),
    # US identifier types
    (r"\bssn\b|social_security", "ssn"),
    (r"\bein\b|employer_id|tax_id_number|\btin\b", "ein"),
    (r"routing_no|routing_number|\baba\b", "routing"),
    (r"\bzip\b|zip_code|zipcode|postal_code", "zip"),
    # India identifier types
    (r"\bpan\b|pan_no|pan_number", "pan"),
    (r"ifsc", "ifsc"),
    (r"gstin|gst_no", "gstin"),
    (r"aadhaar|aadhar|uid_no", "aadhaar"),
    # Global
    (r"\bvin\b|chassis", "vin"),
    (r"email", "email"),
    (r"account_no|acct_no|account_number|bank_account", "account"),
    (r"name|claimant|applicant|payee|holder|insured", "name"),
    (r"policy_no|claim_no|invoice_no|reg_no|_reg$|registration|serial|fir_no|ref_no|imei|engine_no|plate|license_no", "id"),
]


def detect_field_type(field_name: str) -> str:
    fn = str(field_name or "").lower()
    for pat, ftype in _TYPE_PATTERNS:
        if re.search(pat, fn):
            return ftype
    return "text"


# ---------------------------------------------------------------------------
# Format / checksum validators — (valid, reason)
# ---------------------------------------------------------------------------

def validate_pan(v: str) -> Tuple[bool, str]:
    s = normalize_id(v)
    if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", s):
        return False, f"PAN '{s}' does not match AAAAA9999A"
    if s[3] not in "PCHFATBLJG":  # 4th char = holder-type code
        return False, f"PAN '{s}' has invalid holder-type char '{s[3]}'"
    return True, "ok"


def validate_ifsc(v: str) -> Tuple[bool, str]:
    s = normalize_id(v)
    if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", s):
        return False, f"IFSC '{s}' does not match BBBB0XXXXXX"
    return True, "ok"


def validate_gstin(v: str) -> Tuple[bool, str]:
    s = normalize_id(v)
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z]\d|\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9][A-Z][A-Z0-9]", s):
        return False, f"GSTIN '{s}' does not match the 15-char pattern"
    if not (1 <= int(s[:2]) <= 38):
        return False, f"GSTIN '{s}' has invalid state code '{s[:2]}'"
    ok, why = validate_pan(s[2:12])
    if not ok:
        return False, f"GSTIN embedded PAN invalid: {why}"
    return True, "ok"


_VIN_VALUES = {c: v for c, v in zip("ABCDEFGHJKLMNPRSTUVWXYZ", [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9])}
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def validate_vin(v: str) -> Tuple[bool, str]:
    """ISO 3779 — 17 chars, no I/O/Q, position-9 check digit."""
    s = normalize_id(v)
    if len(s) != 17 or re.search(r"[IOQ]", s):
        return False, f"VIN '{s}' must be 17 chars with no I/O/Q"
    total = 0
    for i, ch in enumerate(s):
        val = int(ch) if ch.isdigit() else _VIN_VALUES.get(ch)
        if val is None:
            return False, f"VIN '{s}' has invalid char '{ch}'"
        total += val * _VIN_WEIGHTS[i]
    check = total % 11
    expected = "X" if check == 10 else str(check)
    if s[8] != expected:
        return False, f"VIN '{s}' check digit is '{s[8]}', expected '{expected}'"
    return True, "ok"


# Verhoeff tables (Aadhaar checksum)
_VER_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VER_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def validate_aadhaar(v: str) -> Tuple[bool, str]:
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) != 12 or s[0] in "01":
        return False, "Aadhaar must be 12 digits not starting with 0/1"
    c = 0
    for i, ch in enumerate(reversed(s)):
        c = _VER_D[c][_VER_P[i % 8][int(ch)]]
    if c != 0:
        return False, f"Aadhaar '{s[:4]}********' fails the Verhoeff checksum"
    return True, "ok"


def validate_email(v: str) -> Tuple[bool, str]:
    s = str(v or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", s):
        return False, f"'{s}' is not a valid email"
    return True, "ok"


def validate_phone_in(v: str) -> Tuple[bool, str]:
    s = normalize_phone(v)
    if len(s) != 10 or s[0] not in "6789":
        return False, f"'{v}' is not a valid 10-digit Indian mobile"
    return True, "ok"


def validate_phone_us(v: str) -> Tuple[bool, str]:
    """NANP: 10 digits (optional leading 1); area code + exchange start 2-9."""
    digits = re.sub(r"\D", "", str(v or ""))
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) != 10:
        return False, f"'{v}' is not a valid 10-digit US number"
    if digits[0] in "01" or digits[3] in "01":
        return False, f"'{v}' has an invalid NANP area code or exchange"
    return True, "ok"


def validate_ssn(v: str) -> Tuple[bool, str]:
    """SSA structural rules: no 000/666/9xx area, no 00 group, no 0000 serial."""
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) != 9:
        return False, "SSN must be 9 digits"
    area, group, serial = s[:3], s[3:5], s[5:]
    if area in ("000", "666") or area.startswith("9"):
        return False, f"SSN area '{area}' is never issued"
    if group == "00" or serial == "0000":
        return False, "SSN group/serial cannot be all zeros"
    return True, "ok"


def validate_ein(v: str) -> Tuple[bool, str]:
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) != 9:
        return False, "EIN must be 9 digits (XX-XXXXXXX)"
    if s[:2] in ("00", "07", "08", "09", "17", "18", "19", "28", "29",
                 "49", "69", "70", "78", "79", "89"):
        return False, f"EIN prefix '{s[:2]}' is not assigned by the IRS"
    return True, "ok"


def validate_routing(v: str) -> Tuple[bool, str]:
    """ABA routing number — 9 digits + the 3-7-1 weighted checksum."""
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) != 9:
        return False, "routing number must be 9 digits"
    d = [int(c) for c in s]
    if (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 != 0:
        return False, f"routing number '{s}' fails the ABA checksum"
    return True, "ok"


def validate_zip(v: str) -> Tuple[bool, str]:
    s = str(v or "").strip()
    if not re.fullmatch(r"\d{5}(-\d{4})?", s):
        return False, f"'{s}' is not a valid ZIP / ZIP+4"
    return True, "ok"


# Locale packs: validators active per deployment (FRAUD_LOCALE). Global types
# (email, VIN — the position-9 check digit is the North-American rule, correct
# for the US market and the strictest safe default) apply everywhere. Adding a
# region later = adding a pack, not a refactor.
_VALIDATORS_COMMON = {"vin": validate_vin, "email": validate_email}
_VALIDATORS_BY_LOCALE: Dict[str, Dict[str, Any]] = {
    "us": {"ssn": validate_ssn, "ein": validate_ein, "routing": validate_routing,
           "zip": validate_zip, "phone": validate_phone_us},
    "in": {"pan": validate_pan, "ifsc": validate_ifsc, "gstin": validate_gstin,
           "aadhaar": validate_aadhaar, "phone": validate_phone_in},
}


def _validators_for(locale: Optional[str] = None) -> Dict[str, Any]:
    loc = (locale or _default_locale())
    pack = dict(_VALIDATORS_COMMON)
    pack.update(_VALIDATORS_BY_LOCALE.get(loc, {}))
    # Cross-locale ID types stay validatable when a field NAME names them
    # explicitly (a US app with a field literally called pan_no implies an
    # Indian PAN document) — name-driven, so it can't false-positive.
    for other in _VALIDATORS_BY_LOCALE.values():
        for k, fn in other.items():
            if k != "phone":  # phone rules are locale-exclusive
                pack.setdefault(k, fn)
    return pack


def validate_formats(values: Dict[str, Any], locale: Optional[str] = None) -> List[Dict[str, Any]]:
    """Run checksum/format validators over every field whose NAME maps to a
    validatable id type. Returns failure findings only (explainable)."""
    findings: List[Dict[str, Any]] = []
    validators = _validators_for(locale)
    for field, value in (values or {}).items():
        if value in (None, ""):
            continue
        ftype = detect_field_type(field)
        validator = validators.get(ftype)
        if not validator:
            continue
        ok, reason = validator(str(value))
        if not ok:
            findings.append({
                "field": field, "value": str(value), "check": ftype,
                "severity": "mismatch", "note": reason,
            })
    return findings


# ---------------------------------------------------------------------------
# Cross-check — claimed (record) vs extracted (doc/image) values
# ---------------------------------------------------------------------------

def _values_equal(claimed: Any, extracted: Any, ftype: str,
                  locale: Optional[str] = None) -> Tuple[bool, str, str]:
    """(equal, norm_claimed, norm_extracted) under the type's normalizer.

    Raw-identical strings are ALWAYS equal (two unparseable but identical
    values — 'N/A' vs 'N/A', a two-digit-year date on both sides — must never
    be reported as a mismatch)."""
    if normalize_text(claimed) == normalize_text(extracted) and str(claimed).strip():
        return True, normalize_text(claimed), normalize_text(extracted)
    if ftype == "phone":
        a, b = normalize_phone(claimed), normalize_phone(extracted)
        return a == b and bool(a), a, b
    if ftype == "amount":
        a, b = normalize_amount(claimed), normalize_amount(extracted)
        return a is not None and a == b, str(a), str(b)
    if ftype == "date":
        a, b = normalize_date(claimed, locale), normalize_date(extracted, locale)
        return a is not None and a == b, str(a), str(b)
    if ftype in ("pan", "ifsc", "gstin", "vin", "aadhaar", "account", "id",
                 "ssn", "ein", "routing", "zip"):
        a, b = normalize_id(claimed), normalize_id(extracted)
        return a == b and bool(a), a, b
    if ftype == "name":
        a, b = normalize_name(claimed), normalize_name(extracted)
        # v1: exact-on-normalized OR containment (initials/order handled later);
        # containment downgrades to a warn, handled by the caller.
        return a == b and bool(a), a, b
    a, b = normalize_text(claimed), normalize_text(extracted)
    return a == b and bool(a), a, b


def cross_check(
    claimed: Dict[str, Any],
    extracted: Dict[str, Any],
    types: Optional[Dict[str, str]] = None,
    locale: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Compare record-claimed values against artifact-extracted values.

    Matches keys case-insensitively; compares only keys present on BOTH sides
    (a missing key is not a mismatch — the extractor may not cover it).
    ``types`` optionally pins a field's type; otherwise inferred from the name.
    Returns mismatch findings; equal fields produce nothing.
    """
    findings: List[Dict[str, Any]] = []
    ext_by_key = {str(k).lower(): (k, v) for k, v in (extracted or {}).items()}
    for c_field, c_value in (claimed or {}).items():
        hit = ext_by_key.get(str(c_field).lower())
        if hit is None or c_value in (None, "") or hit[1] in (None, ""):
            continue
        e_field, e_value = hit
        ftype = (types or {}).get(c_field) or detect_field_type(c_field)
        equal, norm_c, norm_e = _values_equal(c_value, e_value, ftype, locale)
        if equal:
            continue
        severity = "mismatch"
        note = f"{ftype} differs after normalization"
        if ftype == "name" and norm_c and norm_e and (norm_c in norm_e or norm_e in norm_c):
            severity = "warn"  # partial name (initials / order) — corroborating only
            note = "partial name overlap — verify manually"
        findings.append({
            "field": c_field, "claimed": str(c_value), "extracted": str(e_value),
            "normalized_claimed": norm_c, "normalized_extracted": norm_e,
            "type": ftype, "severity": severity, "note": note,
        })
    return findings


def arithmetic_check(
    line_items: List[Dict[str, Any]], stated_total: Any,
) -> List[Dict[str, Any]]:
    """Line-item math vs stated total (inflated-invoice tell). Each item may
    carry ``amount`` and optionally ``qty`` × ``rate`` (cross-checked too)."""
    findings: List[Dict[str, Any]] = []
    total = Decimal(0)
    for i, item in enumerate(line_items or []):
        amt = normalize_amount(item.get("amount"))
        qty, rate = normalize_amount(item.get("qty")), normalize_amount(item.get("rate"))
        if amt is None:
            continue
        if qty is not None and rate is not None and qty * rate != amt:
            findings.append({
                "field": f"line_item[{i}]", "severity": "mismatch",
                "note": f"qty×rate = {qty * rate} but amount = {amt}",
            })
        total += amt
    stated = normalize_amount(stated_total)
    if stated is not None and line_items and total != stated:
        findings.append({
            "field": "total", "severity": "mismatch",
            "note": f"line items sum to {total} but stated total is {stated}",
        })
    return findings


# ---------------------------------------------------------------------------
# Artifact fingerprinting (SHA-256 exact-dup) + metadata anomalies
# ---------------------------------------------------------------------------

def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw or b"").hexdigest()


def dhash64(raw: bytes) -> Optional[str]:
    """64-bit difference hash (P2b) — survives recompression/resize where
    SHA-256 breaks. 9×8 grayscale, adjacent-pixel gradient → 16-hex-char hash.
    Dependency-free (PIL only). None when the bytes aren't a decodable image."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw)).convert("L").resize((9, 8), Image.LANCZOS)
        px = list(img.getdata())
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
        return f"{bits:016x}"
    except Exception:  # noqa: BLE001 — not an image / corrupt: no hash
        return None


def dhash_bands(h: str) -> List[str]:
    """4×16-bit LSH bands: images within hamming distance ~≤8 almost always
    share at least one band, so a cheap $in query prefilters candidates and
    exact hamming runs only on that shortlist."""
    return [f"b{i}:{h[i * 4:(i + 1) * 4]}" for i in range(4)]


def hamming_hex(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# Near-dup acceptance: ≤8 differing bits of 64 is a conservative recompression/
# resize band; unrelated photos average ~32.
_DHASH_NEAR_BITS = 8


def _gps_dms_to_decimal(dms: Any, ref: Any) -> float:
    """EXIF GPS (degrees, minutes, seconds) rationals → signed decimal degrees."""
    d, m, s = (float(x) for x in dms)
    val = d + m / 60.0 + s / 3600.0
    return -val if str(ref).strip().upper() in ("S", "W") else val


def image_metadata(raw: bytes) -> Dict[str, Any]:
    """EXIF summary + conservative anomaly flags. Best-effort — an image with
    no EXIF (screenshots, WhatsApp strips it) is NOT itself an anomaly."""
    meta: Dict[str, Any] = {}
    flags: List[str] = []
    try:
        from PIL import Image, ExifTags

        img = Image.open(io.BytesIO(raw))
        exif = img.getexif()
        if exif:
            tag_names = {v: k for k, v in ExifTags.TAGS.items()}
            def _tag(name):
                tid = tag_names.get(name)
                return exif.get(tid) if tid is not None else None
            # DateTimeOriginal (0x9003) lives in the Exif SUB-IFD on real camera
            # files — the top-level getexif() only carries IFD0 tags, so read the
            # sub-IFD first, then fall back to top-level (and IFD0's DateTime).
            try:
                _sub = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
            except Exception:  # noqa: BLE001 — a corrupt sub-IFD must not kill the summary
                _sub = {}
            meta["capture_time"] = str(
                (_sub or {}).get(0x9003) or _tag("DateTimeOriginal")
                or _tag("DateTime") or ""
            ) or None
            meta["camera"] = " ".join(str(x) for x in (_tag("Make"), _tag("Model")) if x) or None
            software = str(_tag("Software") or "")
            if software:
                meta["software"] = software
                if re.search(r"photoshop|gimp|snapseed|lightroom|picsart|canva", software, re.I):
                    flags.append(f"edited_with:{software}")
            gps_ifd = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else None
            if gps_ifd:
                meta["has_gps"] = True
                # Decode to decimal degrees for the EXIF↔claim comparator (E1).
                # Tags: 1=LatRef 2=Lat(DMS) 3=LonRef 4=Lon(DMS). Undecodable
                # coordinates stay absent but VISIBLY (has_gps without gps +
                # gps_error) — never a silent "no GPS".
                try:
                    lat = _gps_dms_to_decimal(gps_ifd[2], gps_ifd.get(1, "N"))
                    lon = _gps_dms_to_decimal(gps_ifd[4], gps_ifd.get(3, "E"))
                    meta["gps"] = {"lat": round(lat, 6), "lon": round(lon, 6)}
                except (KeyError, TypeError, ValueError, ZeroDivisionError) as gexc:
                    meta["gps_error"] = f"GPS IFD present but undecodable: {gexc}"
        meta["dimensions"] = f"{img.width}x{img.height}"
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort, visibly
        meta["metadata_error"] = f"{type(exc).__name__}: {exc}"
    if flags:
        meta["anomalies"] = flags
    return meta


def pdf_metadata(raw: bytes) -> Dict[str, Any]:
    """PDF metadata summary + conservative anomaly flags."""
    meta: Dict[str, Any] = {}
    flags: List[str] = []
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        info = reader.metadata or {}
        producer = str(info.get("/Producer") or "")
        creator = str(info.get("/Creator") or "")
        created, modified = info.get("/CreationDate"), info.get("/ModDate")
        if producer:
            meta["producer"] = producer
        if creator:
            meta["creator"] = creator
        if created:
            meta["created"] = str(created)
        if modified:
            meta["modified"] = str(modified)
        if created and modified and str(modified) > str(created):
            flags.append("modified_after_creation")
        # Flag ONLY image editors as the PDF's authoring tool — producing a
        # DOCUMENT with Photoshop/GIMP/Canva is genuinely odd and a classic
        # tamper tell. Word/LibreOffice are how most LEGITIMATE business PDFs
        # are made, so they are recorded in meta["producer"/"creator"] (the T3
        # reviewer can weigh "bank statement produced by Word" against the
        # task_type) but never scored as an anomaly point.
        if re.search(r"photoshop|gimp|canva|photopea|pixlr", producer + " " + creator, re.I):
            flags.append(f"authoring_tool:{(producer or creator)[:60]}")
        meta["pages"] = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 — best-effort, visibly
        meta["metadata_error"] = f"{type(exc).__name__}: {exc}"
    if flags:
        meta["anomalies"] = flags
    return meta


# ── EXIF ↔ claim comparator (E1) ─────────────────────────────────────────────
# Deterministic checks of an EVIDENCE photo's OWN metadata against the record's
# CLAIMED incident context. WHICH record columns carry that context is declared
# in sources.json ``fraud_screening`` (incident_date_field / location_lat_field /
# location_lon_field / gps_radius_km) and autowired onto the screen — nothing
# here is heuristic, and no declaration ⇒ no check. Design rules:
#   * fire only when BOTH sides carry a value — absent EXIF is a NON-signal
#     (messengers strip it; the WhatsApp rule above), an absent/blank record
#     value is a NON-signal too;
#   * generous tolerances — a false "wrong time/place" flag costs officer trust;
#   * pure CPU, zero LLM; every signal carries an explainable ``why``.

_EXIF_DT_FMTS = ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d")
_GPS_DEFAULT_RADIUS_KM = 10.0
#: Camera-clock / timezone slack: capture must predate the claimed incident by
#: MORE than this many days before it counts.
_CAPTURE_TOLERANCE_DAYS = 1

SIGNAL_CAPTURE_BEFORE = "exif_capture_before_claim"
SIGNAL_GPS_FAR = "exif_gps_far_from_claim"
SIGNAL_CAMERA_FLIP = "camera_model_flip"


def parse_exif_datetime(v: Any, locale: Optional[str] = None) -> Optional[datetime]:
    """EXIF 'YYYY:MM:DD HH:MM:SS' (colon-separated date — EXIF's quirk) →
    datetime; falls back to the shared date normalizer for non-EXIF shapes."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    for fmt in _EXIF_DT_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    iso = normalize_date(s, locale)
    return datetime.strptime(iso, "%Y-%m-%d") if iso else None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (spherical earth — metre precision is
    irrelevant against a ≥10 km gate)."""
    import math

    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _as_float(v: Any) -> Optional[float]:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard


def exif_vs_claim(
    artifact_findings: List[Dict[str, Any]],
    *,
    claimed_incident_date: Any = None,
    claimed_lat: Any = None,
    claimed_lon: Any = None,
    radius_km: Any = None,
    roles: Optional[Dict[str, Optional[str]]] = None,
    locale: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Compare each EVIDENCE photo's EXIF against the record's claimed context.

    Emits (signals, notes):
      * ``exif_capture_before_claim`` — capture time predates the claimed
        incident/report date by > tolerance. A photo cannot show an incident
        that hadn't happened yet.
      * ``exif_gps_far_from_claim``  — EXIF GPS is beyond ``radius_km`` (default
        10 km, deliberately generous) from the claimed site coordinates.
      * ``camera_model_flip``        — ≥2 distinct camera models across one
        record's evidence photoset (corroboration-weight only).

    ``roles`` maps column → artifact_role; only ``evidence`` (or undeclared ⇒
    evidence default) columns are checked — an identity headshot's capture date
    has no relation to the incident. ``notes`` records every VISIBLE skip
    (unparseable claim values), never silently."""
    signals: List[Dict[str, Any]] = []
    notes: List[str] = []
    roles = roles or {}

    incident_dt: Optional[datetime] = None
    if claimed_incident_date not in (None, ""):
        iso = normalize_date(claimed_incident_date, locale)
        if iso:
            incident_dt = datetime.strptime(iso, "%Y-%m-%d")
        else:
            notes.append(
                f"claimed incident date {claimed_incident_date!r} unparseable — "
                "capture-date check skipped"
            )

    lat = _as_float(claimed_lat) if claimed_lat not in (None, "") else None
    lon = _as_float(claimed_lon) if claimed_lon not in (None, "") else None
    if (claimed_lat not in (None, "") or claimed_lon not in (None, "")) and (
        lat is None or lon is None
    ):
        notes.append(
            f"claimed site coordinates ({claimed_lat!r}, {claimed_lon!r}) "
            "unparseable — GPS check skipped"
        )
        lat = lon = None
    # A declared 0 means STRICT (flag any GPS deviation) and must be honored —
    # `or` would swallow it into the generous default, the opposite intent.
    # Only absent/unparseable/negative values fall back to the default.
    _r = _as_float(radius_km)
    radius = _r if _r is not None and _r >= 0 else _GPS_DEFAULT_RADIUS_KM

    cameras: Dict[str, List[str]] = {}
    for f in artifact_findings:
        col = f.get("column")
        if f.get("error"):
            continue
        role = roles.get(col) or "evidence"
        if role != "evidence":
            continue  # identity/supporting artifacts have no incident relation
        meta = f.get("metadata") or {}

        cam = meta.get("camera")
        if cam:
            cameras.setdefault(str(cam), []).append(str(col))

        if incident_dt is not None:
            cap = parse_exif_datetime(meta.get("capture_time"), locale)
            if cap is not None:
                days_before = (incident_dt.date() - cap.date()).days
                if days_before > _CAPTURE_TOLERANCE_DAYS:
                    signals.append({
                        "signal": SIGNAL_CAPTURE_BEFORE,
                        "column": col,
                        "capture_time": meta.get("capture_time"),
                        "claimed_incident_date": incident_dt.strftime("%Y-%m-%d"),
                        "days_before": days_before,
                        "why": (
                            f"EXIF says this photo was captured {days_before} days "
                            "BEFORE the claimed incident/report date — it cannot "
                            "show the claimed incident."
                        ),
                    })

        if lat is not None and lon is not None:
            gps = meta.get("gps") or {}
            g_lat, g_lon = _as_float(gps.get("lat")), _as_float(gps.get("lon"))
            if g_lat is not None and g_lon is not None:
                dist = haversine_km(g_lat, g_lon, lat, lon)
                if dist > radius:
                    signals.append({
                        "signal": SIGNAL_GPS_FAR,
                        "column": col,
                        "gps": {"lat": g_lat, "lon": g_lon},
                        "claimed_site": {"lat": lat, "lon": lon},
                        "distance_km": round(dist, 1),
                        "radius_km": radius,
                        "why": (
                            f"EXIF GPS puts this photo {round(dist, 1)} km from the "
                            f"claimed site (gate: {radius} km) — it was taken "
                            "somewhere else."
                        ),
                    })

    if len(cameras) >= 2:
        signals.append({
            "signal": SIGNAL_CAMERA_FLIP,
            "cameras": {c: sorted(set(cols)) for c, cols in cameras.items()},
            "why": (
                f"{len(cameras)} distinct camera models across one record's "
                "evidence photoset — corroborating signal only (photos may "
                "legitimately come from different submitters)."
            ),
        })
    return signals, notes


# ── Payment-proof verification (E4) ──────────────────────────────────────────
# "I already paid — here's the receipt." The receipt's extracted reference is
# looked up in the REAL payment ledger (the sources.json-declared dataset,
# read server-side by key); this comparator judges the result. Doctrine:
#   * "reference not found" is a FACT — the ledger either has it or it doesn't
#     (the caller guarantees the lookup actually ran; lookup failure is a
#     visible error, never treated as not-found);
#   * a found-and-matching payment is VERIFICATION (the customer is right) —
#     rendered positively, never scored;
#   * OCR-noise guards: amount tolerance %, date window days;
#   * pure CPU, zero LLM; every signal carries an explainable ``why``.

SIGNAL_PAY_NOT_FOUND = "payment_ref_not_found"
SIGNAL_PAY_AMOUNT = "payment_amount_mismatch"
SIGNAL_PAY_DATE = "payment_date_mismatch"
SIGNAL_PAY_PARTY = "payment_party_mismatch"


def payment_doc_attached(
    doc_columns: Optional[List[str]],
    artifact_findings: Optional[List[Dict[str, Any]]],
    config_label: str = "payment_proof",
) -> Tuple[bool, Optional[str]]:
    """F3 gate — may a document-pinned verification run for THIS record?

    The check is pinned to the ontology-tagged document column(s)
    (``doc_columns``). It runs only when at least one of them resolved to an
    actual document during artifact screening; otherwise it is skipped with a
    VISIBLE note — never run against whichever other bill happens to be
    attached, and never silently.

    ``config_label`` names the sources.json key the note should point at
    (the verify_against loop passes ``verify_against[<name>].doc_column`` so
    a mis-stamped block sends the operator to the right config, not to
    payment_proof).

    Returns (attached, skip_note). No doc_columns configured ⇒ the config
    predates pinning and autowire would have dropped it — treat as not
    attached so an unpinned check can never run.
    """
    cols = [str(c) for c in (doc_columns or []) if c]
    if not cols:
        return False, (f"{config_label} config has no pinned document "
                       "column(s) — check skipped; republish so autowire "
                       "re-stamps it from the ontology")
    ok = [a.get("column") for a in (artifact_findings or [])
          if a.get("column") in cols and not a.get("error")]
    if ok:
        return True, None
    return False, (
        f"no pinned document attached (column(s) {', '.join(cols)} "
        f"empty or unreadable) — check skipped; the claimed value was NOT "
        f"verified either way")


def payment_proof_check(
    *,
    doc_ref: Any,
    doc_amount: Any = None,
    doc_date: Any = None,
    doc_party: Any = None,
    ledger_row: Optional[Dict[str, Any]],
    cfg: Dict[str, Any],
    locale: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool, List[str]]:
    """Compare an extracted payment-proof against the ledger row the reference
    resolved to. Returns ``(signals, verified, notes)`` — ``verified`` is True
    only when the reference was FOUND and no declared comparison mismatched.
    The caller resolves ``ledger_row`` (case-insensitive keys already applied)
    and only calls this when a ``doc_ref`` was actually extracted."""
    signals: List[Dict[str, Any]] = []
    notes: List[str] = []
    ledger_name = cfg.get("ledger_dataset") or "the payment ledger"
    ref_str = str(doc_ref).strip()

    if ledger_row is None:
        signals.append({
            "signal": SIGNAL_PAY_NOT_FOUND,
            "reference": ref_str,
            "ledger": ledger_name,
            "why": (
                f"The submitted proof cites payment reference '{ref_str}', but "
                f"{ledger_name} has NO such payment — the proof references a "
                "transaction that never happened."
            ),
        })
        return signals, False, notes

    def _ledger(field_key: str) -> Any:
        col = cfg.get(field_key)
        return ledger_row.get(col) if col else None

    # A DECLARED ledger-side comparison that cannot run (the ledger's value is
    # missing or unparseable while the document carries one) blocks the
    # VERIFIED verdict: "verified" must mean "every declared comparison ran
    # clean", never "the comparisons that happened to be runnable ran clean" —
    # otherwise a garbage ledger column silently rubber-stamps doctored
    # receipts with the screen's highest-trust output.
    ledger_gaps: List[str] = []

    # Amount — normalized, within tolerance %.
    l_amt = _ledger("amount_field")
    if doc_amount not in (None, ""):
        d_amt = normalize_amount(doc_amount)
        g_amt = normalize_amount(l_amt) if l_amt not in (None, "") else None
        if d_amt is None:
            notes.append(f"document amount {doc_amount!r} unparseable — amount check skipped")
        elif cfg.get("amount_field") and g_amt is None:
            ledger_gaps.append(
                f"ledger amount ({cfg.get('amount_field')}={l_amt!r}) missing "
                "or unparseable — amount comparison could not run")
        elif g_amt is not None:
            tol = Decimal(str(cfg.get("amount_tolerance_pct", 1.0))) / Decimal(100)
            limit = abs(g_amt) * tol
            if abs(d_amt - g_amt) > limit:
                signals.append({
                    "signal": SIGNAL_PAY_AMOUNT,
                    "reference": ref_str,
                    "document_amount": str(d_amt), "ledger_amount": str(g_amt),
                    "why": (
                        f"Payment '{ref_str}' exists, but the proof shows "
                        f"{d_amt} while the ledger recorded {g_amt} — the "
                        "amount on the document does not match the real payment."
                    ),
                })

    # Date — normalized, within the window.
    l_date = _ledger("date_field")
    if doc_date not in (None, ""):
        d_iso = normalize_date(doc_date, locale)
        g_iso = normalize_date(l_date, locale) if l_date not in (None, "") else None
        if not d_iso:
            notes.append(f"document date {doc_date!r} unparseable — date check skipped")
        elif cfg.get("date_field") and not g_iso:
            ledger_gaps.append(
                f"ledger date ({cfg.get('date_field')}={l_date!r}) missing or "
                "unparseable — date comparison could not run")
        elif g_iso:
            delta = abs((datetime.strptime(d_iso, "%Y-%m-%d")
                         - datetime.strptime(g_iso, "%Y-%m-%d")).days)
            if delta > int(cfg.get("date_window_days", 3)):
                signals.append({
                    "signal": SIGNAL_PAY_DATE,
                    "reference": ref_str,
                    "document_date": d_iso, "ledger_date": g_iso,
                    "days_apart": delta,
                    "why": (
                        f"Payment '{ref_str}' exists, but the proof is dated "
                        f"{d_iso} while the ledger recorded {g_iso} "
                        f"({delta} days apart)."
                    ),
                })

    # Party — the strongest reuse tell: a GENUINE receipt, but someone else's.
    l_party = _ledger("party_field")
    if doc_party not in (None, ""):
        if cfg.get("party_field") and l_party in (None, ""):
            ledger_gaps.append(
                f"ledger party ({cfg.get('party_field')}) missing — party "
                "comparison could not run")
        elif l_party not in (None, ""):
            if normalize_id(str(doc_party)) != normalize_id(str(l_party)):
                signals.append({
                    "signal": SIGNAL_PAY_PARTY,
                    "reference": ref_str,
                    "document_party": str(doc_party), "ledger_party": str(l_party),
                    "why": (
                        f"Payment '{ref_str}' is REAL but belongs to "
                        f"'{l_party}', not this case's party '{doc_party}' — a "
                        "genuine receipt reused by someone else."
                    ),
                })

    for gap in ledger_gaps:
        notes.append(f"{gap}; NOT fully verified")
    verified = not signals and not ledger_gaps
    return signals, verified, notes


# ── E7: photoset-timing cluster (pencil-whipping) — CORROBORATION ONLY ───────
SIGNAL_PHOTOSET_TIMING = "photoset_timing_cluster"

#: Photos of DIFFERENT records captured within this many minutes of one of
#: this record's photos count toward the cluster.
_E7_WINDOW_MINUTES = 15
#: Fire only when at least this many OTHER records cluster — one neighbor is
#: two back-to-back legitimate site visits.
_E7_MIN_OTHER_RECORDS = 2


async def photoset_timing_cluster(
    *,
    tenant_id: Optional[str],
    app_slug: Optional[str],
    record_ref: Optional[str],
    capture_times: List[datetime],
    window_minutes: int = _E7_WINDOW_MINUTES,
    min_other_records: int = _E7_MIN_OTHER_RECORDS,
) -> Optional[Dict[str, Any]]:
    """E7 — pencil-whipping tell: photos of ≥N distinct OTHER records captured
    within minutes of this record's photos (nobody genuinely inspects three
    sites in fifteen minutes). CORROBORATION ONLY (weight 1): it raises the T3
    gate, never flags alone, and never counts as an issue in the screen output.

    App-scoped (same app ≈ same inspection workflow) — a per-submitter grain
    would need the inspector key on every fingerprint; the advisory says so.
    Returns one signal dict, or None."""
    if not (tenant_id and app_slug and capture_times):
        return None

    def _naive_utc(dt: datetime) -> datetime:
        """One tz convention for BOTH sides of every comparison. EXIF parses
        are naive and Mongo round-trips naive-UTC (client not tz_aware) —
        an aware datetime anywhere would make the subtraction below raise."""
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt

    times = [_naive_utc(t) for t in capture_times]
    col = _fingerprints_col()
    window = timedelta(minutes=max(1, int(window_minutes)))
    lo = min(times) - window
    hi = max(times) + window
    other_records: Dict[str, str] = {}
    async for doc in col.find(
        {"tenant_id": tenant_id, "app_slug": app_slug,
         "capture_time": {"$gte": lo, "$lte": hi}},
        {"capture_time": 1, "refs": {"$slice": -5}},
    ).limit(500):
        cap = doc.get("capture_time")
        if not isinstance(cap, datetime):
            continue
        cap_utc = _naive_utc(cap)
        if not any(abs((cap_utc - t).total_seconds()) <= window.total_seconds()
                   for t in times):
            continue
        for r in (doc.get("refs") or []):
            rref = r.get("record_ref")
            if rref and not same_record_ref(rref, record_ref):
                other_records.setdefault(str(rref), str(cap_utc))
    if len(other_records) < max(1, int(min_other_records)):
        return None
    sample = sorted(other_records.items())[:5]
    return {
        "signal": SIGNAL_PHOTOSET_TIMING,
        "corroboration_only": True,
        "other_record_count": len(other_records),
        "window_minutes": int(window_minutes),
        "other_records": [{"record_ref": k, "captured_at": v} for k, v in sample],
        "why": (
            f"Photos on {len(other_records)} OTHER case(s) in this app were "
            f"captured within {int(window_minutes)} minutes of this record's "
            "photos — sites are not genuinely inspected minutes apart "
            "(pencil-whipping tell). Corroboration only: this never flags a "
            "case by itself."
        ),
    }


# ── Generic cross-dataset verification (plan F4 — the E4 shape reused) ───────
SIGNAL_VERIFY_NOT_FOUND = "verify_ref_not_found"
SIGNAL_VERIFY_MISMATCH = "verify_field_mismatch"


def verify_against_check(
    *,
    name: str,
    doc_ref: Any,
    doc_values: Dict[str, Any],
    target_row: Optional[Dict[str, Any]],
    compare: List[Dict[str, Any]],
    target_name: str = "the target dataset",
    locale: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool, List[str]]:
    """Generic document-vs-dataset verification: the caller resolved
    ``target_row`` by key (case-insensitive keys applied; None = not found)
    and passes the document's extracted ``doc_values`` (lowercased keys).
    Same doctrine as payment_proof_check: not-found is fact-grade (the lookup
    RAN), a missing side is a NON-signal, unparseable values become notes, and
    verified means found + EVERY declared comparison ran clean — a target-side
    value that is present but unparseable blocks VERIFIED (a garbage target
    column must never rubber-stamp a doctored document)."""
    signals: List[Dict[str, Any]] = []
    notes: List[str] = []
    target_gaps = 0
    ref_str = str(doc_ref).strip()

    if target_row is None:
        signals.append({
            "signal": SIGNAL_VERIFY_NOT_FOUND,
            "check": name,
            "reference": ref_str,
            "target": target_name,
            "why": (
                f"[{name}] The document cites reference '{ref_str}', but "
                f"{target_name} has NO such record — the document references "
                "something that does not exist in the system of record."
            ),
        })
        return signals, False, notes

    for c in (compare or []):
        d_raw = doc_values.get(str(c.get("doc_field") or "").lower())
        t_raw = target_row.get(str(c.get("target_field") or "").lower())
        if d_raw in (None, "") or t_raw in (None, ""):
            continue  # absent side = non-signal, never an alarm
        ctype = c.get("type") or "text"
        mismatch = None
        if ctype == "amount":
            d, t = normalize_amount(d_raw), normalize_amount(t_raw)
            if d is None:
                notes.append(f"[{name}] document {c.get('doc_field')}={d_raw!r} "
                             "unparseable — comparison skipped")
                continue
            if t is None:
                notes.append(
                    f"[{name}] target {c.get('target_field')}={t_raw!r} "
                    "unparseable — comparison could not run; NOT fully verified")
                target_gaps += 1
                continue
            tol = Decimal(str(c.get("tolerance_pct", 1.0))) / Decimal(100)
            if abs(d - t) > abs(t) * tol:
                mismatch = (str(d), str(t))
        elif ctype == "date":
            d, t = normalize_date(d_raw, locale), normalize_date(t_raw, locale)
            if not d:
                notes.append(f"[{name}] document {c.get('doc_field')}={d_raw!r} "
                             "unparseable — comparison skipped")
                continue
            if not t:
                notes.append(
                    f"[{name}] target {c.get('target_field')}={t_raw!r} "
                    "unparseable — comparison could not run; NOT fully verified")
                target_gaps += 1
                continue
            delta = abs((datetime.strptime(d, "%Y-%m-%d")
                         - datetime.strptime(t, "%Y-%m-%d")).days)
            if delta > int(c.get("window_days", 3)):
                mismatch = (d, f"{t} ({delta} days apart)")
        elif ctype == "id":
            if normalize_id(str(d_raw)) != normalize_id(str(t_raw)):
                mismatch = (str(d_raw), str(t_raw))
        else:  # text
            if str(d_raw).strip().casefold() != str(t_raw).strip().casefold():
                mismatch = (str(d_raw), str(t_raw))
        if mismatch:
            signals.append({
                "signal": SIGNAL_VERIFY_MISMATCH,
                "check": name,
                "reference": ref_str,
                "field": c.get("target_field"),
                "document_value": mismatch[0],
                "target_value": mismatch[1],
                "why": (
                    f"[{name}] Record '{ref_str}' exists in {target_name}, but "
                    f"the document's {c.get('doc_field')} ({mismatch[0]}) does "
                    f"not match the recorded {c.get('target_field')} "
                    f"({mismatch[1]})."
                ),
            })

    return signals, (not signals and not target_gaps), notes


# ── E6: declarative date rules (ontology-driven, record-local) ───────────────
SIGNAL_DATE_RULE = "date_rule_violation"


def date_rules_check(
    record_row: Dict[str, Any],
    rules: List[Dict[str, Any]],
    locale: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Evaluate the ontology's declarative date rules against the record's own
    values (read server-side by key — never agent-supplied).

    Each rule: {name, earlier_field, later_field, min_days_between=0,
    max_days_between=None}. Fires when (later - earlier) < min (with min=0
    that is the plain ordering rule: an inspection dated before its work
    order) or > max when set (stale documents). A missing or unparseable side
    is a NON-signal with a visible note — absence of data is never an alarm.
    """
    signals: List[Dict[str, Any]] = []
    notes: List[str] = []
    row_ci = {str(k).lower(): v for k, v in (record_row or {}).items()}
    for rule in (rules or []):
        name = rule.get("name") or "date_rule"
        e_field = str(rule.get("earlier_field") or "")
        l_field = str(rule.get("later_field") or "")
        e_raw = row_ci.get(e_field.lower())
        l_raw = row_ci.get(l_field.lower())
        if e_raw in (None, "") or l_raw in (None, ""):
            continue  # absent side — non-signal
        e_iso, l_iso = normalize_date(e_raw, locale), normalize_date(l_raw, locale)
        if not e_iso or not l_iso:
            notes.append(
                f"[{name}] unparseable date ({e_field}={e_raw!r}, "
                f"{l_field}={l_raw!r}) — rule skipped")
            continue
        delta = (datetime.strptime(l_iso, "%Y-%m-%d")
                 - datetime.strptime(e_iso, "%Y-%m-%d")).days
        min_days = rule.get("min_days_between")
        min_days = int(min_days) if isinstance(min_days, (int, float)) else 0
        max_days = rule.get("max_days_between")
        if delta < min_days:
            why = (
                f"[{name}] {l_field} ({l_iso}) is only {delta} day(s) after "
                f"{e_field} ({e_iso}) — the rule requires at least {min_days}."
                if delta >= 0 else
                f"[{name}] {l_field} ({l_iso}) is BEFORE {e_field} ({e_iso}) "
                f"— impossible ordering ({-delta} day(s) earlier)."
            )
            signals.append({"signal": SIGNAL_DATE_RULE, "rule": name,
                            "earlier_field": e_field, "later_field": l_field,
                            "days_between": delta, "why": why})
        elif isinstance(max_days, (int, float)) and delta > int(max_days):
            signals.append({
                "signal": SIGNAL_DATE_RULE, "rule": name,
                "earlier_field": e_field, "later_field": l_field,
                "days_between": delta,
                "why": (f"[{name}] {l_field} ({l_iso}) is {delta} day(s) after "
                        f"{e_field} ({e_iso}) — beyond the allowed "
                        f"{int(max_days)}."),
            })
    return signals, notes


# ── E5: bank-statement running-balance reconciliation ────────────────────────
SIGNAL_STATEMENT_BREAK = "statement_chain_break"

#: Absolute per-row slack for OCR digit noise, in currency units.
_STMT_EPSILON = Decimal("0.02")
#: Fire only on this many INDEPENDENT breaks — one break is usually an OCR
#: misread or a skipped row; a fabricated statement breaks repeatedly.
_STMT_MIN_BREAKS = 2


def statement_reconciliation(
    rows: List[Dict[str, Any]],
    *,
    min_breaks: int = _STMT_MIN_BREAKS,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Running-balance chain check over extracted statement rows (E5): each
    row's balance must equal the prior balance ± its transaction. Fabricated
    statements are typically composed per-row (amounts invented to look right)
    and break the chain repeatedly; OCR noise breaks it once.

    Row shape (per doc_extract): {balance, credit?, debit?, amount?} — `amount`
    is signed when credit/debit aren't split. Rows missing a parseable balance
    are skipped with a note (and break the chain-link around them — the check
    resumes from the next parseable balance, it never guesses).

    Returns (signals, notes): at most ONE signal, carrying every break, and
    only when breaks >= min_breaks.
    """
    notes: List[str] = []
    breaks: List[Dict[str, Any]] = []
    checked = 0
    prev_balance: Optional[Decimal] = None
    prev_idx: Optional[int] = None
    for i, row in enumerate(rows or []):
        r = {str(k).lower(): v for k, v in (row or {}).items()}
        bal = normalize_amount(r.get("balance"))
        if bal is None:
            if r.get("balance") not in (None, ""):
                notes.append(f"row {i}: balance {r.get('balance')!r} unparseable "
                             "— chain link skipped")
            prev_balance, prev_idx = None, None
            continue
        if prev_balance is not None:
            credit = normalize_amount(r.get("credit"))
            debit = normalize_amount(r.get("debit"))
            amt = normalize_amount(r.get("amount"))
            _garbled = any(
                r.get(k) not in (None, "") and v is None
                for k, v in (("credit", credit), ("debit", debit), ("amount", amt)))
            if _garbled or (credit is None and debit is None and amt is None):
                # NO parseable transaction on this row (different column names
                # like deposit/withdrawal, or OCR garbage). Silently assuming
                # txn=0 would turn every genuine movement into a 'break' and
                # flag an honest statement as fabricated — instead the link is
                # skipped VISIBLY unless the balance genuinely didn't move.
                if abs(bal - prev_balance) <= _STMT_EPSILON:
                    checked += 1        # zero-activity row — consistent chain
                else:
                    if len(notes) < 5:
                        notes.append(
                            f"row {i}: transaction values (credit/debit/amount) "
                            "missing or unreadable while the balance moved — "
                            "chain link skipped, not counted as a break")
                    prev_balance, prev_idx = bal, i
                    continue
            else:
                txn = (credit or Decimal(0)) - (debit or Decimal(0))
                if credit is None and debit is None and amt is not None:
                    txn = amt
                expected = prev_balance + txn
                checked += 1
                if abs(bal - expected) > _STMT_EPSILON:
                    breaks.append({
                        "row": i, "prev_row": prev_idx,
                        "prev_balance": str(prev_balance), "txn": str(txn),
                        "expected_balance": str(expected), "stated_balance": str(bal),
                    })
        prev_balance, prev_idx = bal, i
    if len(breaks) >= max(1, int(min_breaks)) and checked:
        return ([{
            "signal": SIGNAL_STATEMENT_BREAK,
            "breaks": breaks[:10],
            "break_count": len(breaks),
            "rows_checked": checked,
            "why": (
                f"The statement's running balance breaks {len(breaks)} time(s) "
                f"across {checked} checked row(s) — each balance should equal "
                "the prior balance plus that row's transaction. Repeated "
                "breaks are the fabricated-statement tell (single breaks are "
                "treated as OCR noise and NOT flagged)."
            ),
        }], notes)
    if breaks:
        notes.append(
            f"{len(breaks)} single running-balance break(s) across {checked} "
            f"row(s) — below the {min_breaks}-break threshold, treated as OCR "
            "noise (not flagged)")
    return [], notes


# ── Document CONTENT reuse (the doc twin of the image dHash tier) ────────────
# A photo's identity is its PIXELS; a document's identity is its TEXT. So a PDF
# re-exported / re-saved / stripped of metadata is byte-DIFFERENT (SHA-256 misses
# it) yet content-identical — the classic "same invoice on three claims" that the
# image tiers (dHash/CLIP) can't see because they never run on a PDF. SimHash over
# the text layer restores a near-dup tier for documents, reusing the SAME band
# prefilter + hamming machinery as dHash.
#
# Scanned/image PDFs have no text layer → no fingerprint (recorded, never silent).

_TEXT_MAX_PAGES = 12          # bound the CPU on a huge PDF
_TEXT_MIN_TOKENS = 24         # ENTROPY GUARD — see simhash64
# SimHash is far tighter than dHash: near-identical text differs in only a few
# bits, unrelated text averages ~32. 3/64 is deliberately conservative — a false
# "reused document" is the one outcome this whole stack exists to avoid.
_TEXT_NEAR_BITS = 3


def pdf_text(raw: bytes) -> Optional[str]:
    """The PDF's text layer (first ``_TEXT_MAX_PAGES`` pages), or None when there
    is none (a scanned/image PDF) or it can't be parsed. Best-effort by design."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for page in reader.pages[:_TEXT_MAX_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — one bad page must not kill the rest
                continue
        text = " ".join(parts).strip()
        return text or None
    except Exception:  # noqa: BLE001 — best-effort; caller records the miss
        return None


def _text_tokens(text: str) -> List[str]:
    """Normalised tokens: case/whitespace/punctuation-insensitive, so a re-export
    that only changes layout still fingerprints identically."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def simhash64(text: str) -> Optional[str]:
    """64-bit SimHash over token shingles → 16-hex, comparable with ``hamming_hex``.

    Returns None when the text is too thin to identify a document
    (< ``_TEXT_MIN_TOKENS``). That guard is essential, NOT an optimisation: a
    fingerprint over a handful of tokens collides across unrelated documents, and
    every collision would surface as a bogus "reused document" fraud signal. Better
    no signal than a false one."""
    tokens = _text_tokens(text)
    if len(tokens) < _TEXT_MIN_TOKENS:
        return None
    # Shingles (word bigrams) — order-sensitive, so reordered boilerplate doesn't
    # read as the same document.
    shingles = [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]
    vector = [0] * 64
    for sh in shingles:
        h = int(hashlib.sha256(sh.encode("utf-8")).hexdigest()[:16], 16)
        for bit in range(64):
            vector[bit] += 1 if (h >> bit) & 1 else -1
    value = 0
    for bit in range(64):
        if vector[bit] > 0:
            value |= 1 << bit
    return f"{value:016x}"


def qualify_record_ref(
    dataset_ref: Optional[str], record_key: Optional[str]
) -> Optional[str]:
    """A tenant-globally-unique record identity: ``"<dataset_ref>:<record_key>"``.

    The reuse detectors match across the WHOLE tenant (the fingerprint store keys on
    ``(tenant_id, sha256)``, NOT app_slug — a recycled artifact must be caught even
    when it is resubmitted through a different Decision App). That makes a BARE
    record key an unsafe identity: two datasets that both number rows from 1 collide,
    and a collision reads as "same record" → the reuse is silently NOT flagged.
    Qualifying with the catalogue dataset ref makes the identity unique across
    apps/datasets, while two apps bound to the SAME dataset still agree on the same
    record — correct, since re-screening one real record is never fraud.

    Falls back to the bare key when no dataset ref resolves (unbound / headless
    callers with no record binding) — the pre-existing behaviour."""
    key = (record_key or "").strip()
    if not key:
        return None
    ds = (dataset_ref or "").strip()
    return f"{ds}:{key}" if ds else key


def bare_record_key(record_ref: Optional[str]) -> Optional[str]:
    """The record-key part of a (possibly dataset-qualified) ``record_ref``.
    Lives HERE, beside qualify_record_ref/same_record_ref, because this module
    owns the ref grammar — a second parser elsewhere would silently diverge
    the day the qualification format changes."""
    if not record_ref:
        return None
    return str(record_ref).rsplit(":", 1)[-1] or None


def same_record_ref(a: Optional[str], b: Optional[str]) -> bool:
    """True when two ``record_ref``s identify the SAME record.

    Strict equality once both sides are dataset-qualified — that is the collision
    fix. LEGACY COMPAT: refs written before qualification are a bare record key, so
    when EXACTLY ONE side is unqualified we compare only the key part. Without that,
    every record fingerprinted before this change would flag ITSELF as a duplicate on
    its next screening (+3 bogus points — precisely the false alarm this scoping
    exists to prevent). The leniency is one-sided, cannot weaken a
    qualified-vs-qualified comparison, and decays as the derived fingerprint store
    refreshes. (A bare key containing a literal ':' would be read as qualified —
    accepted: record keys are ids, and the fallback is the old behaviour.)"""
    if not a or not b:
        return False
    if a == b:
        return True
    if (":" in a) != (":" in b):
        return a.rsplit(":", 1)[-1] == b.rsplit(":", 1)[-1]
    return False


def _fingerprints_col():
    """Env-routed collection handle (same lazy-main pattern as analysis_rubrics)."""
    import main  # deferred — avoids import cycle

    if getattr(main, "_db", None) is None:
        raise RuntimeError("Database not initialised")
    name = _FINGERPRINT_COLLECTION
    try:
        if main.current_env() == "test":
            name = main._test_collection_name(_FINGERPRINT_COLLECTION)
    except Exception:  # noqa: BLE001 — env unknown ⇒ prod collection (safe default)
        pass
    return main._db[name]


# Keyed by ROUTED collection name — the env routing is per-request (test_
# prefix via contextvar), so a plain module bool would latch after whichever
# env screens first and leave the OTHER env's collection unindexed.
_fp_indexes_ensured: set = set()


async def _ensure_fp_indexes() -> None:
    col = _fingerprints_col()
    if col.name in _fp_indexes_ensured:
        return
    await col.create_index([("tenant_id", 1), ("sha256", 1)], unique=True)
    await col.create_index([("tenant_id", 1), ("dhash_bands", 1)])
    # Document text-SimHash band prefilter — the doc twin of dhash_bands.
    await col.create_index([("tenant_id", 1), ("text_bands", 1)])
    # E7 photoset-timing window scan (pencil-whipping corroboration).
    await col.create_index([("tenant_id", 1), ("app_slug", 1), ("capture_time", 1)])
    _fp_indexes_ensured.add(col.name)


async def record_and_check_fingerprint(
    *,
    tenant_id: Optional[str],
    app_slug: Optional[str],
    sha256: str,
    modality: str,
    task_type: str,
    item_id: str,
    record_ref: Optional[str] = None,
    dhash: Optional[str] = None,
    text_simhash: Optional[str] = None,
    capture_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Write-through fingerprint upsert + duplicate lookups.

    Returns::

        {duplicate, prior_refs[],          # SHA-256 byte-identical (P1)
         phash_near_dups[],                # dHash hamming ≤ 8 (P2b) — survives
                                           #   recompression / resize
         stored_vector?}                   # cached full-dim CLIP vector, when
                                           #   this image was embedded before

    Pointers only — no artifact payload is stored.
    """
    await _ensure_fp_indexes()
    col = _fingerprints_col()
    now = datetime.now(timezone.utc)
    ref = {"item_id": item_id, "record_ref": record_ref, "seen_at": now}
    update: Dict[str, Any] = {
        "$setOnInsert": {
            "tenant_id": tenant_id, "sha256": sha256,
            "modality": modality, "first_seen": now,
        },
        "$set": {"last_seen": now, "app_slug": app_slug, "task_type": task_type},
        "$push": {"refs": {"$each": [ref], "$slice": -50}},
    }
    if dhash:
        update["$set"]["dhash"] = dhash
        update["$set"]["dhash_bands"] = dhash_bands(dhash)
    if text_simhash:
        # Same 4×16-bit LSH scheme as dhash, in its own field so image and
        # document bands can never cross-match.
        update["$set"]["text_simhash"] = text_simhash
        update["$set"]["text_bands"] = dhash_bands(text_simhash)
    if capture_time is not None:
        # EXIF capture instant — a property of the image itself, stored once
        # per unique content hash. Powers the E7 photoset-timing cluster
        # (pencil-whipping corroboration).
        update["$set"]["capture_time"] = capture_time
    doc = await col.find_one_and_update(
        {"tenant_id": tenant_id, "sha256": sha256},
        update,
        upsert=True,
        return_document=False,  # the PRE-update doc → its refs are the priors
    )
    # A ref is a PRIOR only when it comes from a DIFFERENT record. item_id is
    # LLM-chosen and varies across re-runs of the same case, so it can only be
    # the dedup key when record binding is absent on either side — otherwise a
    # re-screened case would flag itself as a duplicate (+3 bogus points).
    def _is_prior(r: Dict[str, Any]) -> bool:
        r_rec = r.get("record_ref")
        if record_ref and r_rec:
            return not same_record_ref(r_rec, record_ref)
        return r.get("item_id") != item_id

    prior_refs = [
        {"item_id": r.get("item_id"), "record_ref": r.get("record_ref"),
         "seen_at": str(r.get("seen_at"))}
        for r in ((doc or {}).get("refs") or [])
        if _is_prior(r)
    ]
    out: Dict[str, Any] = {"duplicate": bool(prior_refs), "prior_refs": prior_refs[:10]}
    if doc and doc.get("embed_vector"):
        out["stored_vector"] = doc["embed_vector"]

    # dHash near-dup (different bytes, same picture): band-prefiltered
    # candidates, exact hamming locally. Only OTHER records count.
    if dhash:
        candidates = await col.find(
            {"tenant_id": tenant_id, "dhash_bands": {"$in": dhash_bands(dhash)},
             "sha256": {"$ne": sha256}},
            {"sha256": 1, "dhash": 1, "refs": {"$slice": -3}},
        ).to_list(length=30)
        near = []
        for c in candidates:
            if not c.get("dhash"):
                continue
            dist = hamming_hex(dhash, c["dhash"])
            c_refs = [r for r in (c.get("refs") or [])
                      if not record_ref
                      or not same_record_ref(r.get("record_ref"), record_ref)]
            if dist <= _DHASH_NEAR_BITS and c_refs:
                near.append({
                    "hamming_bits": dist,
                    "refs": [{"item_id": r.get("item_id"),
                              "record_ref": r.get("record_ref")} for r in c_refs],
                })
        if near:
            out["phash_near_dups"] = sorted(near, key=lambda x: x["hamming_bits"])[:5]

    # Document text near-dup (different bytes, SAME content — a re-exported /
    # re-saved / metadata-stripped PDF). The doc twin of the dHash tier: identical
    # band prefilter + local hamming, tighter threshold. Only OTHER records count.
    if text_simhash:
        candidates = await col.find(
            {"tenant_id": tenant_id, "text_bands": {"$in": dhash_bands(text_simhash)},
             "sha256": {"$ne": sha256}},
            {"sha256": 1, "text_simhash": 1, "refs": {"$slice": -3}},
        ).to_list(length=30)
        tnear = []
        for c in candidates:
            if not c.get("text_simhash"):
                continue
            dist = hamming_hex(text_simhash, c["text_simhash"])
            c_refs = [r for r in (c.get("refs") or [])
                      if not record_ref
                      or not same_record_ref(r.get("record_ref"), record_ref)]
            if dist <= _TEXT_NEAR_BITS and c_refs:
                tnear.append({
                    "hamming_bits": dist,
                    "refs": [{"item_id": r.get("item_id"),
                              "record_ref": r.get("record_ref")} for r in c_refs],
                })
        if tnear:
            out["text_near_dups"] = sorted(tnear, key=lambda x: x["hamming_bits"])[:5]
    return out


async def store_embed_vector(*, tenant_id: Optional[str], sha256: str, vector: List[float]) -> None:
    """Persist the FULL-dim embedding on the fingerprint doc (embed once, ever;
    future index-dim changes re-index locally from here — zero API calls)."""
    await _fingerprints_col().update_one(
        {"tenant_id": tenant_id, "sha256": sha256},
        {"$set": {"embed_vector": vector, "embed_dim": len(vector)}},
    )


async def artifact_flags(
    *,
    raw: bytes,
    mime: str,
    tenant_id: Optional[str],
    app_slug: Optional[str],
    modality: str,
    task_type: str,
    item_id: str,
    record_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Compose the artifact signals for one analyzed artifact: SHA-256 exact
    dedup + metadata anomalies (T0), dHash near-dup + CLIP similar-image
    across cases (T1/P2b, images), and text-SimHash near-dup (documents).
    Best-effort by design — analysis must never fail because enrichment did —
    but failures are RECORDED in the output (never silent), per RULE #1."""
    import asyncio

    is_pdf = (mime or "").lower() == "application/pdf"

    def _cpu_parse():
        # pypdf/PIL parsing + dhash + text extraction/SimHash are CPU-bound —
        # never run them on the event loop (a 30MB scanned PDF would stall every
        # concurrent request).
        h = sha256_hex(raw)
        meta = pdf_metadata(raw) if is_pdf else image_metadata(raw)
        d = None if is_pdf else dhash64(raw)
        # Documents: the text layer IS the identity (a re-export changes bytes,
        # not content). Images keep the pixel tiers instead.
        t = simhash64(pdf_text(raw) or "") if is_pdf else None
        return h, meta, d, t

    sha, meta, dh, tsh = await asyncio.to_thread(_cpu_parse)
    out: Dict[str, Any] = {"sha256": sha, "metadata": meta}
    # EXIF capture instant (images) — persisted on the fingerprint doc for the
    # E7 photoset-timing cluster. Absent/strip-metadata images stay None.
    _cap_dt = None if is_pdf else parse_exif_datetime((meta or {}).get("capture_time"))
    if not is_pdf and dh is None:
        # Never silent: an undecodable image means the ENTIRE near-dup tier is
        # off for this artifact — the officer must see that, not a clean flag.
        out["dhash_error"] = "image undecodable — near-duplicate checks skipped"
    if is_pdf and tsh is None:
        # Same rule for documents: a scanned/image PDF (or one with too little
        # text to identify) gets NO content near-dup tier — say so rather than
        # let a clean flag imply "checked and fine".
        out["text_fingerprint_error"] = (
            "no usable text layer (scanned or too short) — document "
            "near-duplicate checks skipped"
        )

    if tenant_id is None:
        # No app tenant and no org claim — writing into a null-tenant
        # namespace would merge unrelated apps' evidence. Skip VISIBLY.
        out["cross_case_checks"] = "skipped: tenant unresolved (no app tenant, no org claim)"
        log.warning("[FRAUD] artifact_flags: tenant unresolved — cross-case checks skipped")
        return out

    stored_vector = None
    try:
        dup = await record_and_check_fingerprint(
            tenant_id=tenant_id, app_slug=app_slug, sha256=out["sha256"],
            modality=modality, task_type=task_type, item_id=item_id,
            record_ref=record_ref, dhash=dh, text_simhash=tsh,
            capture_time=_cap_dt,
        )
        stored_vector = dup.pop("stored_vector", None)
        out.update(dup)
    except Exception as exc:  # noqa: BLE001 — visible degradation, not silent
        log.warning("[FRAUD] fingerprint store unavailable: %s", exc)
        out["fingerprint_error"] = f"{type(exc).__name__}: {exc}"

    # CLIP similar-image index (P2b) — images only, embedded ONCE per unique
    # content hash (cached full vector rides the fingerprint doc).
    if not is_pdf:
        try:
            from config import get_settings
            from fraud_image_index import index_and_search

            settings = get_settings()
            if settings.image_embed_api_key:
                res = await index_and_search(
                    settings=settings, raw=raw, mime=mime, sha256=out["sha256"],
                    tenant_id=tenant_id, app_slug=app_slug, record_ref=record_ref,
                    item_id=item_id, task_type=task_type,
                    stored_vector=stored_vector,
                )
                fresh_vec = res.pop("vector", None)
                if fresh_vec:
                    await store_embed_vector(
                        tenant_id=tenant_id, sha256=out["sha256"], vector=fresh_vec
                    )
                if res:
                    out["image_index"] = res
        except Exception as exc:  # noqa: BLE001 — visible degradation, not silent
            # ERROR, not warning: whatever the cause (embed timeout on the
            # free-tier model, Milvus down), the similar-image fraud tier is
            # OFF for this artifact — a screening gap the operator must see.
            log.error("[FRAUD] image index unavailable — similar-image tier "
                      "OFF for this artifact: %s", exc)
            out["image_index_error"] = f"{type(exc).__name__}: {exc}"
    return out
