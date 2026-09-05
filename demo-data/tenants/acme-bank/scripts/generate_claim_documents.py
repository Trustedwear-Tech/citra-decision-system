# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0

"""Generate the claim documents that actually exist as FILES, and prove they are
safe to upload before anything is uploaded.

Why this is careful: `fraud_checks` fingerprints REAL BYTES at three tiers —
SHA-256 (exact), dHash (images), and SimHash over a PDF's text layer at a
threshold of 3 bits in 64. Generating 1,090 documents from one template with the
claim number swapped would put nearly every pair inside that threshold, and the
claims app would report a reused document on every case. The fraud signal would
become noise and the real findings would be buried.

So every document is composed from randomised pools — different garage, vehicle,
parts, hospital, doctor, diagnosis, amounts, dates, remarks — seeded per
document_id so a re-run reproduces the same bytes. Then EVERY pair is checked
with the service's own simhash64/hamming_hex before upload. Exactly one
byte-identical pair is intended (the reused repair estimate); anything else that
lands near-duplicate is a bug in this generator, and the run aborts.

Scope: documents on OPEN claims only (intimated / under_survey) — the ~1,090 an
officer can actually reach through the triage queue. The other ~8,275 sit on
settled history nobody opens; their file_url is cleared so nothing looks
openable that is not. Photographs are NOT generated: a synthetic damage photo
looks synthetic and would undercut the demo more than an absent one.

    python generate_claim_documents.py            # generate + verify, no upload
    python generate_claim_documents.py --upload   # ...then upload to S3
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List

import psycopg2

SCRIPT_DIR = Path(__file__).resolve().parent
TENANT_DIR = SCRIPT_DIR.parent
REPO = TENANT_DIR.parents[2]
OUT_DIR = SCRIPT_DIR / "_generated_docs"
SERVICE = REPO / "smart-app-service"
sys.path.insert(0, str(SERVICE))

PG = dict(host="localhost", port=int(os.getenv("ACME_BANK_PG_PORT", "15444")),
          dbname="acme_bank",
          user="acme_bank", password="acme_bank_demo_pw")

#: The one duplicate the demo is built on: CLM-NEEDLE-003's repair estimate is
#: byte-identical to this older claim's. Everything else must be unique.
DUP_SOURCE_CLAIM = "CLM-2026-000001"
DUP_TARGET_CLAIM = "CLM-NEEDLE-003"
DUP_DOC_TYPE = "repair_estimate"

#: Rendered from a template, a document differs from its siblings in only a few
#: tokens. These pools are what push pairs apart in SimHash space.
GARAGES = ["Sai Auto Works", "Krishna Motors", "Deccan Car Care", "Meridian Autotech",
           "Sharma Body Shop", "Coastal Auto Garage", "Prime Wheels Service",
           "Ganesh Automobiles", "Nova Motor Works", "Rajdhani Auto Point"]
CITIES = ["Pune", "Mumbai", "Bengaluru", "Hyderabad", "Chennai", "Jaipur",
          "Ahmedabad", "Kochi", "Indore", "Nagpur", "Surat", "Lucknow"]
VEHICLES = ["Maruti Swift VXi", "Hyundai i20 Asta", "Tata Nexon XZ", "Honda City ZX",
            "Mahindra XUV300", "Kia Seltos HTK", "Toyota Glanza G", "Renault Kwid RXT",
            "Skoda Rapid Rider", "Volkswagen Polo Comfortline"]
PARTS = ["front bumper", "rear bumper", "left fender", "right fender", "bonnet",
         "windscreen", "headlamp assembly", "tail lamp", "door shell",
         "side mirror", "radiator", "condenser", "suspension arm", "alloy wheel",
         "brake caliper", "quarter panel", "boot lid", "grille", "fog lamp",
         "wheel arch", "roof rail", "tail gate", "axle shaft", "silencer"]
OPERATIONS = ["denting", "painting", "panel beating", "wheel alignment",
              "electrical rewiring", "glass fitment", "underbody treatment",
              "suspension overhaul", "air-conditioning recharge"]
ASSESSORS = ["R. Venkatesh", "A. Bhattacharya", "S. Nair", "P. Deshmukh",
             "M. Fernandes", "K. Rathore", "T. Chandrasekhar", "V. Joshi"]
REMARKS = [
    "Impact confined to the front left quarter; chassis alignment within tolerance.",
    "Damage consistent with a low-speed rear-end collision reported by the insured.",
    "Water ingress observed in the boot well; carpet replacement recommended.",
    "Paint blending required on the adjacent panel to match the existing shade.",
    "Airbag modules intact; no deployment recorded on the diagnostic scan.",
    "Prior repair evident on the opposite panel, unrelated to the present claim.",
    "Salvage value of the replaced parts deducted from the assessed figure.",
    "Vehicle driven to the workshop under its own power; no towing charge applies.",
]
HOSPITALS = ["Apollo Speciality Hospital", "Fortis Healthcare", "Manipal Hospital",
             "Sunrise Multispeciality", "Ruby Hall Clinic", "KIMS Hospital",
             "Lilavati Medical Centre", "Narayana Health City", "Yashoda Hospital"]
DOCTORS = ["Dr. Anand Rao", "Dr. Meenakshi Iyer", "Dr. Sanjay Gokhale",
           "Dr. Ritu Malhotra", "Dr. Faisal Ahmed", "Dr. Latha Subramanian",
           "Dr. Nikhil Chaudhary", "Dr. Preeti Bansal"]
DIAGNOSES = [
    "acute febrile illness with thrombocytopenia",
    "community-acquired pneumonia, right lower lobe",
    "acute appendicitis with localised peritonitis",
    "unstable angina, managed conservatively",
    "renal calculus with obstructive uropathy",
    "cellulitis of the left lower limb",
    "acute gastroenteritis with moderate dehydration",
    "fracture of the distal radius, closed reduction",
]
PROCEDURES = ["intravenous fluid and antibiotic therapy", "laparoscopic appendicectomy",
              "ureteroscopy with stent placement", "closed reduction and casting",
              "coronary angiography", "nebulisation and chest physiotherapy",
              "wound debridement under local anaesthesia"]
POLICE_STATIONS = ["Shivajinagar", "Andheri East", "Koramangala", "Banjara Hills",
                   "T. Nagar", "Malviya Nagar", "Navrangpura", "Ernakulam North"]
SECTIONS = ["379 IPC (theft)", "427 IPC (mischief causing damage)",
            "337 IPC (causing hurt by act endangering life)",
            "279 IPC (rash driving)", "304A IPC (causing death by negligence)"]
FIR_OPENINGS = [
    "the complainant attended the station in person and stated the following.",
    "a written complaint was received at the station and registered on it.",
    "information was received on the control room line and reduced to writing.",
    "the complainant appeared with the vehicle papers and lodged this report.",
    "an oral complaint was recorded and read back to the complainant.",
]
FIR_CIRCUMSTANCES = [
    "The vehicle had been parked outside the residence overnight and the damage "
    "was noticed the following morning.",
    "The incident occurred on the highway service road during heavy rain and poor "
    "visibility.",
    "The complainant had stepped away from the market parking bay for a short "
    "while and returned to find the vehicle disturbed.",
    "A collision occurred at the junction when the other driver failed to give "
    "way, and the other party left before details were exchanged.",
    "The vehicle was left in the office basement over the weekend and the loss "
    "was discovered on Monday.",
    "The complainant was returning from a family function late at night when the "
    "incident took place near the flyover.",
    "The vehicle was standing in the hospital car park while the complainant "
    "attended a relative in the ward.",
]
FIR_PROPERTY = [
    "the insured vehicle together with its stereo and toolkit",
    "the insured vehicle, with damage to the front assembly",
    "the insured vehicle and personal effects left in the cabin",
    "the insured vehicle, both offside doors affected",
    "the insured vehicle and a spare wheel carried in the boot",
]
FIR_WITNESS = [
    "No eyewitness has come forward so far.",
    "Two residents of the locality have been examined and their statements taken.",
    "The watchman on duty has been examined; he did not observe the incident.",
    "Closed-circuit footage from a nearby shop has been requisitioned.",
    "A passer-by provided a partial registration number, which is being verified.",
]
FIR_ACTIONS = [
    "the scene was inspected and a panchanama drawn in the presence of witnesses.",
    "the vehicle has been photographed and released to the complainant on bond.",
    "a search of the surrounding area was conducted with no recovery so far.",
    "the case has been entered in the station diary and taken up for enquiry.",
    "the complainant has been advised to produce the original registration papers.",
]
ENDORSEMENTS = [
    "Endorsement 1: geographical extent limited to India.",
    "Endorsement 2: cover extended to include electrical and electronic fittings.",
    "Endorsement 3: nil depreciation applies for the first two policy years.",
    "Endorsement 4: consumables and engine protection excluded unless opted.",
    "Endorsement 5: personal accident cover for the owner-driver included.",
    "Endorsement 6: legal liability to paid driver covered on premium receipt.",
    "Endorsement 7: hospital cash benefit payable after twenty-four hours.",
    "Endorsement 8: pre-existing conditions subject to the waiting period.",
    "Endorsement 9: maternity benefit excluded for the first four years.",
    "Endorsement 10: voluntary deductible opted, premium discounted accordingly.",
    "Endorsement 11: anti-theft device fitted and certified by the installer.",
    "Endorsement 12: cover suspended while the vehicle is off the road.",
]
POLICY_CLAUSES = [
    "Claims must be intimated within the stated window; late intimation is "
    "considered on documented merit.",
    "Cover operates only while premium stands paid and the policy is in force on "
    "the date of loss.",
    "The insurer may appoint a licensed surveyor before admitting liability.",
    "Contribution applies where any other policy covers the same loss.",
    "Salvage remains the property of the insurer once a total loss is settled.",
    "Fraudulent or exaggerated claims render this policy void from inception.",
]


# ── a minimal PDF with a real text layer ────────────────────────────────────
def _esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_pdf(lines: List[str]) -> bytes:
    """A one-page PDF whose text pypdf can extract.

    Hand-rolled rather than pulling in reportlab: the demo data generator should
    not add a dependency to the service venv, and the fraud check only needs a
    readable text layer.
    """
    content = ["BT", "/F1 10 Tf", "50 800 Td", "13 TL"]
    for ln in lines:
        content.append(f"({_esc(ln)}) Tj")
        content.append("T*")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1", "replace")

    objs: List[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]"
        b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>",
        b"<</Length " + str(len(stream)).encode() + b">>\nstream\n" + stream
        + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objs)+1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<</Size {len(objs)+1}/Root 1 0 R>>\nstartxref\n"
              f"{xref_at}\n%%EOF\n".encode())
    return out.getvalue()


# ── document bodies ─────────────────────────────────────────────────────────
def _money(x: float) -> str:
    return f"Rs {x:,.2f}"


def repair_estimate(rng, doc, claim) -> List[str]:
    garage, city = rng.choice(GARAGES), rng.choice(CITIES)
    vehicle = rng.choice(VEHICLES)
    reg = (f"{rng.choice(['MH','KA','TN','GJ','RJ','DL','TS','KL'])}"
           f"{rng.randint(1,49):02d}{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
           f"{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.randint(1000,9999)}")
    picked = rng.sample(PARTS, rng.randint(3, 6))
    lines = [
        f"{garage} - Authorised Repair Estimate",
        f"{city} branch | GSTIN 27{rng.randint(10**8, 10**9-1)}Z{rng.randint(1,9)}",
        "",
        f"Claim reference: {claim['claim_id']}",
        f"Policy: {claim['policy_no']}",
        f"Vehicle: {vehicle}   Registration: {reg}",
        f"Date of loss: {claim['loss_date']}   Estimate raised: {doc['uploaded_at'].date()}",
        "",
        "Parts and labour",
    ]
    subtotal = 0.0
    for p in picked:
        qty = rng.randint(1, 2)
        rate = round(rng.uniform(1800, 24000), 2)
        amt = qty * rate
        subtotal += amt
        lines.append(f"  {p} x{qty} at {_money(rate)} = {_money(amt)}")
    for op in rng.sample(OPERATIONS, rng.randint(1, 3)):
        hrs = rng.randint(2, 14)
        rate = round(rng.uniform(350, 900), 2)
        amt = hrs * rate
        subtotal += amt
        lines.append(f"  {op}, {hrs} hours at {_money(rate)} per hour = {_money(amt)}")
    gst = round(subtotal * 0.18, 2)
    lines += [
        "",
        f"Subtotal {_money(subtotal)}   GST at 18 percent {_money(gst)}",
        f"Total assessed {_money(subtotal + gst)}",
        "",
        f"Surveyor remarks: {rng.choice(REMARKS)}",
        f"Assessed by {rng.choice(ASSESSORS)}, licensed surveyor.",
        "This estimate is valid for thirty days from the date of issue.",
    ]
    return lines


def discharge_summary(rng, doc, claim) -> List[str]:
    hosp, doctor = rng.choice(HOSPITALS), rng.choice(DOCTORS)
    diag, proc = rng.choice(DIAGNOSES), rng.choice(PROCEDURES)
    days = rng.randint(2, 9)
    return [
        f"{hosp} - Discharge Summary",
        f"{rng.choice(CITIES)} | Registration {rng.randint(10000,99999)}",
        "",
        f"Claim reference: {claim['claim_id']}   Policy: {claim['policy_no']}",
        f"Date of admission: {claim['loss_date']}   Length of stay: {days} days",
        f"Treating consultant: {doctor}",
        "",
        f"Presenting complaint and diagnosis: {diag}.",
        f"Management: the patient underwent {proc} and was monitored on the ward.",
        "Vitals remained stable through the admission and the patient was afebrile",
        "for forty-eight hours before discharge.",
        "",
        "Investigations: complete blood count, renal and liver function, chest",
        f"radiograph and {rng.choice(['ultrasound abdomen','ECG','CT of the abdomen','MRI'])}.",
        "",
        f"Condition on discharge: {rng.choice(['stable','improved','symptom free'])}.",
        f"Advice: review in {rng.randint(1,4)} weeks; complete the prescribed course.",
        f"Signed {doctor} for {hosp}.",
    ]


def hospital_invoice(rng, doc, claim) -> List[str]:
    hosp = rng.choice(HOSPITALS)
    room = round(rng.uniform(2500, 12000), 2)
    days = rng.randint(2, 9)
    items = [("Room and nursing", room * days),
             ("Consultant visits", round(rng.uniform(2000, 15000), 2)),
             ("Investigations", round(rng.uniform(3000, 26000), 2)),
             ("Pharmacy and consumables", round(rng.uniform(1500, 34000), 2))]
    if rng.random() < 0.5:
        items.append(("Operation theatre", round(rng.uniform(12000, 60000), 2)))
    total = sum(a for _n, a in items)
    lines = [f"{hosp} - Tax Invoice",
             f"Invoice number {rng.randint(100000, 999999)} | {rng.choice(CITIES)}",
             "",
             f"Claim reference: {claim['claim_id']}   Policy: {claim['policy_no']}",
             f"Billing period ending {doc['uploaded_at'].date()}", ""]
    for name, amt in items:
        lines.append(f"  {name}: {_money(amt)}")
    lines += ["", f"Total payable {_money(total)}",
              "Payment received in full by the insured, subject to reimbursement.",
              "This is a computer generated invoice and needs no signature."]
    return lines


def fir_copy(rng, doc, claim) -> List[str]:
    """An FIR narrative built from several independent pools.

    The first version drew one location and one verb from short lists, so two
    reports could differ by a single word and land 3 bits apart in SimHash —
    which is exactly a "reused document" signal on two unrelated claims. The
    narrative is now assembled from separate opening / circumstance / property /
    witness / action pools, so the combinations do not repeat across a corpus
    this size.
    """
    ps, district = rng.choice(POLICE_STATIONS), rng.choice(CITIES)
    return [
        f"First Information Report - {ps} Police Station",
        f"District {district} | FIR number {rng.randint(1,999):03d}/2026",
        "",
        f"Claim reference: {claim['claim_id']}   Policy: {claim['policy_no']}",
        f"Date of occurrence: {claim['loss_date']}   "
        f"Reported: {doc['uploaded_at'].date()}",
        f"Sections invoked: {rng.choice(SECTIONS)}",
        "",
        f"Complaint: {rng.choice(FIR_OPENINGS)}",
        f"{rng.choice(FIR_CIRCUMSTANCES)}",
        f"Property involved: {rng.choice(FIR_PROPERTY)}, valued by the complainant "
        f"at approximately {_money(round(rng.uniform(35000, 900000), -2))}.",
        f"{rng.choice(FIR_WITNESS)}",
        "",
        f"Action taken: {rng.choice(FIR_ACTIONS)}",
        f"Investigating officer: {rng.choice(['SI','ASI','PSI','Inspector'])} "
        f"{rng.choice(['Kadam','Pillai','Grewal','Mahato','Barman','Reddy','Salunke','Dubey','Nayak','Chettiar'])}.",
        f"Case {rng.choice(['under investigation','transferred to the crime branch','pending forensic report'])}; "
        f"final report to follow.",
    ]


def policy_copy(rng, doc, claim) -> List[str]:
    """The schedule of the ACTUAL policy.

    The first version was boilerplate with a policy number swapped in — 463
    documents of 63 words each, and 190 pairs landed inside the 3-bit SimHash
    threshold. A certified schedule that does not state the cover is not a
    document anyway, so the fix and the realism are the same thing: the product,
    the sums, the dates, the nominee and the endorsements all come from the
    policy row, which differs from every other policy.
    """
    endorsements = rng.sample(ENDORSEMENTS, rng.randint(2, 4))
    excess = rng.choice([1000, 2000, 2500, 5000, 7500, 10000])
    return [
        "Acme Bank & Insurance Ltd - Policy Schedule (certified copy)",
        f"Policy number {claim['policy_no']}   Class: {claim.get('line') or 'general'}",
        f"Product: {claim.get('product_name') or 'General cover'}",
        "",
        f"Period of insurance: {claim.get('start_date')} to {claim.get('end_date')}",
        f"Sum insured: {_money(float(claim.get('sum_insured') or 0))}",
        f"Annual premium: {_money(float(claim.get('premium_annual') or 0))}",
        f"Nominee on record: {claim.get('nominee_name') or 'not recorded'}",
        f"Compulsory excess: {_money(float(excess))} per claim.",
        "",
        f"Claim reference on file: {claim['claim_id']}",
        f"Issued at the {rng.choice(CITIES)} branch, register folio "
        f"{rng.randint(10**7, 10**8-1)}.",
        "",
        "Endorsements attaching to this schedule:",
    ] + [f"  {e}" for e in endorsements] + [
        "",
        f"{rng.choice(POLICY_CLAUSES)}",
        f"Countersigned by {rng.choice(ASSESSORS)} for the underwriting division.",
    ]


BUILDERS = {
    "repair_estimate": repair_estimate,
    "discharge_summary": discharge_summary,
    "invoice": hospital_invoice,
    "fir": fir_copy,
    "policy_copy": policy_copy,
}


def fetch_targets() -> List[Dict[str, Any]]:
    """Documents on OPEN claims — what an officer can actually reach. Photographs
    are excluded deliberately (see the module docstring)."""
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("""
        select d.document_id, d.claim_id, d.doc_type, d.uploaded_at,
               c.policy_no, c.loss_date, c.claim_type,
               p.product_name, p.line, p.sum_insured, p.premium_annual,
               p.start_date, p.end_date, p.nominee_name
        from claim_documents d
        join claims c on c.claim_id = d.claim_id
        join policies p on p.policy_no = c.policy_no
        where c.status in ('intimated','under_survey')
          and d.doc_type <> 'damage_photo'
        order by d.document_id
    """)
    cols = [x[0] for x in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    # The duplicate's SOURCE claim is settled history, so it is not in the open
    # set — pull its estimate in explicitly, or the pair cannot exist.
    cur.execute("""
        select d.document_id, d.claim_id, d.doc_type, d.uploaded_at,
               c.policy_no, c.loss_date, c.claim_type,
               p.product_name, p.line, p.sum_insured, p.premium_annual,
               p.start_date, p.end_date, p.nominee_name
        from claim_documents d join claims c on c.claim_id = d.claim_id
        join policies p on p.policy_no = c.policy_no
        where d.claim_id = %s and d.doc_type = %s
    """, (DUP_SOURCE_CLAIM, DUP_DOC_TYPE))
    for r in cur.fetchall():
        rows.append(dict(zip(cols, r)))
    conn.close()
    return rows


def build_all(rows: List[Dict[str, Any]]) -> Dict[str, bytes]:
    """document_id -> pdf bytes. Seeded per document so a re-run is identical."""
    out: Dict[str, bytes] = {}
    dup_bytes = None
    for r in rows:
        builder = BUILDERS.get(r["doc_type"])
        if not builder:
            continue
        rng = random.Random(f"acme-bank/{r['document_id']}")
        claim = dict(r)          # the whole row: claim AND policy fields
        pdf = make_pdf(builder(rng, r, claim))
        out[r["document_id"]] = pdf
        if r["claim_id"] == DUP_SOURCE_CLAIM and r["doc_type"] == DUP_DOC_TYPE:
            dup_bytes = pdf
    # THE intended duplicate: the same estimate filed twice, byte for byte.
    if dup_bytes is not None:
        for r in rows:
            if r["claim_id"] == DUP_TARGET_CLAIM and r["doc_type"] == DUP_DOC_TYPE:
                out[r["document_id"]] = dup_bytes
    return out


def verify(docs: Dict[str, bytes]) -> int:
    """Fingerprint every document with the SERVICE's own code and refuse to ship
    a corpus that would cry fraud. Returns the number of problems."""
    from fraud_checks import hamming_hex, pdf_text, sha256_hex, simhash64

    # pdf_text() imports pypdf inside a try/except and returns None when it is
    # not installed -- indistinguishable, downstream, from a PDF that genuinely
    # has no text layer. Every document then reads as "thin" and this function
    # reports the CORPUS as broken when the truth is a missing dependency in
    # whichever interpreter is running it. That happened on a real --fresh seed:
    # 1013 perfectly good documents, all reported unfingerprintable, upload
    # refused, empty bucket. Probe once, up front, and say the true thing.
    try:
        import pypdf  # noqa: F401
    except ImportError:
        print("  FAIL - pypdf is not installed in this interpreter, so no PDF's")
        print("         text can be read and EVERY document would be reported as")
        print("         having too little text. That is a fault in this environment,")
        print("         not in the corpus. Install it and re-run:")
        print(f"           {sys.executable} -m pip install pypdf")
        return 1

    by_sha: Dict[str, List[str]] = {}
    sims: Dict[str, str] = {}
    thin = []
    for did, raw in docs.items():
        by_sha.setdefault(sha256_hex(raw), []).append(did)
        text = pdf_text(raw)
        sh = simhash64(text or "")
        if sh:
            sims[did] = sh
        else:
            thin.append(did)

    problems = 0
    collisions = {h: ids for h, ids in by_sha.items() if len(ids) > 1}
    print(f"  documents            : {len(docs)}")
    print(f"  distinct SHA-256     : {len(by_sha)}")
    print(f"  byte-identical groups: {len(collisions)} (exactly 1 is intended)")
    for h, ids in collisions.items():
        print(f"     {h[:16]}… -> {ids}")
    if len(collisions) != 1:
        print("  FAIL — expected exactly one intended duplicate pair")
        problems += 1

    if thin:
        print(f"  FAIL — {len(thin)} document(s) have too little text to fingerprint: "
              f"{thin[:5]}")
        problems += 1

    # Near-duplicate sweep. 3/64 bits is the service's own threshold; anything
    # inside it would surface as "reused document" on an unrelated claim.
    dup_ids = {i for ids in collisions.values() for i in ids}
    items = [(d, s) for d, s in sims.items()]
    near = []
    for i in range(len(items)):
        di, si = items[i]
        for j in range(i + 1, len(items)):
            dj, sj = items[j]
            if di in dup_ids and dj in dup_ids:
                continue          # the intended pair is identical by design
            if hamming_hex(si, sj) <= 3:
                near.append((di, dj))
    print(f"  near-duplicate pairs : {len(near)} (must be 0)")
    for a, b in near[:10]:
        print(f"     {a} ~ {b}")
    if near:
        print("  FAIL — widen the content pools; these would read as reused documents")
        problems += 1
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--upload", action="store_true",
                    help="upload to S3 after verification passes")
    args = ap.parse_args()

    rows = fetch_targets()
    print(f"targets: {len(rows)} document(s) on open claims (+ the duplicate source)")
    docs = build_all(rows)
    OUT_DIR.mkdir(exist_ok=True)
    for did, raw in docs.items():
        (OUT_DIR / f"{did}.pdf").write_bytes(raw)
    print(f"written to {OUT_DIR}")

    print("\nverifying against the service's own fingerprinting:")
    problems = verify(docs)
    if problems:
        print("\nREFUSING TO UPLOAD — fix the generator first.")
        return 1
    print("\nPASS — safe to upload.")
    if args.upload:
        from upload_claim_documents import upload   # noqa: WPS433 — optional step
        upload(rows, docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
