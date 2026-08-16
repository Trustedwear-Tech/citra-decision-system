# `case_signature` — teaching an app to learn

> **Terminology (doctrine — get this right in everything you author and say to
> the BA):** the org's **RULES are the SOP** — authored, live-fetched, supreme;
> nothing learned ever overrides them. What officers teach through corrections
> is **JUDGEMENT** — their experience, covering what the SOP does not spell out
> (e.g. "declared earnings look fine but the tax filing doesn't corroborate
> them" — no SOP clause lists that tell; an experienced officer's eye does).
> One officer's correction becomes an **individual judgement** — used
> immediately, labeled as one officer's view; several officers agreeing makes
> it a **team judgement**. Never call the learned layer "rules".
>
> **"Facet" is OUR word, not the BA's.** They have never heard it and it tells
> them nothing. What a facet family actually is, in their terms, is a **business
> category the app files its learning under** — the way *they* already group
> these cases when they talk about them. Same for the rest of the machinery:
> `case_signature`, `family`, `scope_facets`, `__unknown` are implementation
> names and must never appear in a sentence a BA reads. Say what it DOES.
>
> | Never say | Say |
> |---|---|
> | "I'll author a case_signature with five facet families" | "I'll file what this app learns under **product**, **amount band**, **FOIR band**, **LTV band** and **sourcing channel**" |
> | "the clause is scoped to these facets" | "that lesson gets used again on cases in the same categories — and only those" |
> | "scope_facets ⊆ case_facets" | "it applies when the case matches all of those" |
> | "this facet derives `__unknown`" | "we couldn't tell for this case, so it won't be grouped on that" |
> | "the facet family has high cardinality" | "there are too many different values there for it to be a useful grouping" |
>
> The test for any sentence you put in front of a BA: could a credit officer
> who has never seen this product read it and say "yes, that's how we think
> about these"? If not, rewrite it.

**What it is, in one line:** the set of **business categories** this app files
its learning under. When an officer corrects the app on one case, these decide
which *other* cases that lesson gets re-used on.

Optional block on `app_spec`. It does **not** decide whether the app learns —
every app learns either way. It decides whether what it learns can be **scoped**
and **categorised**.

Without it: the app still records every officer correction and still forms
judgements from them, but they are *global* — they apply to every case of that
kind rather than to the ones the facets would have narrowed them to.

With it: a judgement can say "for **theft** claims **over $25k**, require a
police report number" and apply only to those cases.

Author it whenever the app makes a judgement a human reviews. Skip it for
read-only dashboards.

### A human must confirm the families (CS-04)

The families are not an implementation detail. They decide the SCOPE of every
judgement the app will ever learn — retrieval is `scope_facets ⊆ case_facets`,
so whatever you choose here is what "cases like this one" means, permanently.
You choose them from the SOP and the bound columns, and nobody else was ever
required to look.

So **`case_signature` must carry a confirmation** before it will publish:

| Field | What it is |
|---|---|
| `confirmed_by` | the person who reviewed the families — their identity, not "the BA" |
| `confirmed_at` | when |
| `confirmed_families` | the exact list you put in front of them |

**Where this happens: IN THE BUILD CHAT.** That is the BA's surface — they are
in a conversation with you, and they will not see the app's spec screen until
much later, when they go looking at their list of apps. So propose the families
as a chat turn, let them accept or edit, and record the result. There is no
separate screen to send them to and none is needed.

CS-04 checks one thing: **`confirmed_families` must match the families actually
declared.** That is the divergence nobody can see afterwards — you proposed
four, the BA agreed to four, and six shipped. Everything else about the
exchange lives in the skill, because it is behaviour, not structure.

It is deliberately NOT an identity check. Who built an app is already answered
by RBAC and the audit trail, and it is not the governed question anyway — who
changes DATA is. Requiring proof of which human you spoke to would add
machinery around a problem the platform has already solved.

So be honest about what this is: a light guard, not a gate. An agent determined
to skip the conversation can set `confirmed_families` to match. What it cannot
do is have the conversation, hear "drop LTV", and ship LTV anyway.

Two notes. On the **hand-edit** path — a person editing the spec on the Decision
Apps screen, or pressing Confirm on the case-signature panel there — the server
records this from the authenticated user, because a human is demonstrably
looking at it. That is a later review surface, not the build path. And when you
CHANGE the families on an edit, re-confirm: the old record describes a different
list, and CS-04 will say so.

### Optional to ADD. Never optional to KEEP.

"Optional" describes the first publish, and it has been read too broadly. Once
an app has a signature, **every clause it learns is scoped to those facet
families** — and retrieval is `scope_facets ⊆ case_facets`. Republish without
the block and every case derives `case_facets: []`, so nothing the app has
learned can match again. The clauses do not disappear; they sit in the store
reading `active` and never fire. That is the worst failure shape there is:
knowledge that looks present and is not.

This happened. `dealer-limit-review` was rebuilt, the rebuild re-authored the
spec from scratch and simply did not re-emit the block, and clause memory
stopped with no error anywhere.

So, when rebuilding or editing an app that already has one:

* **copy `case_signature` across from `SEED_APP_SPEC` unchanged** unless the BA
  asked for a change — publish rule **CS-03** rejects a version that drops it;
* if a facet's column was renamed, **rename the family and list the old name in
  that facet's `aliases`** — publish migrates the existing clauses in place
  rather than orphaning them;
* if a family is genuinely gone, expect its clauses to be **orphaned** at
  publish, and say so to the BA. That is deliberate and visible, unlike losing
  the whole block.

---

## 1. Where the vocabulary comes from

**You are not designing a taxonomy.** Most of it already exists in the bound
dataset's columns. A column whose description reads

```
category: no_power | billing_issue | meter_fault | theft_report | other
```

is already a closed vocabulary. Promoting it to a facet is one entry. Look for:

* columns whose description lists `a | b | c` values → `enum`
* amount / score / count columns → `band`
* nullable evidence columns (`police_report_no`, `photos`) → `presence`
* date pairs (`policy_start_date`, `loss_date`) → `age_band`
* fraud screening this app already enables → `signal`

Aim for **4–8 families**. More is not better — see §5.

### The columns are a starting point. The BA is the authority.

What you can lift from columns is how the DATA is shaped. How the BUSINESS
groups these cases is a different question, and only the BA can answer it. So
propose your list, and when they replace part of it with a category of their
own — "we'd really split these by dealer tier" — that is a better answer than
yours, not an objection to handle.

Their word and the column name will often differ: *ticket size* is
`amount_requested`, *vintage* is `relationship_start_date`, *programme* may be
a value inside a column rather than a column of its own. Look for what their
concept maps to before deciding it is not there, and remember that most
business categories are a DERIVATION (`band`, `presence`, `age_band`) rather
than a stored column.

**Name the family in the BA's words, not the column's.** The family name is
rendered verbatim on the officer's decision card — `ticket_size` appears as
"Ticket size". Naming it `amount_band` because that is what the column is
called pushes our vocabulary onto their officers for the life of the app.

The only ground to push back on is **cardinality**, and say it in their terms:
a category with hundreds of distinct values groups nothing, so offer the banded
version instead of refusing.

---

## 1b. Do NOT author reason codes

`reason_codes` is **deprecated. Author none.** An app that declares no taxonomy
learns exactly as well as one that does — better, in practice.

An officer's correction is now **free text (min 10 words) plus the fields they
changed**. Nothing asks them to pick a label.

Why it was removed, so it does not get reinvented:

* It was app-specific vocabulary a model had to invent, and models invent
  DECLINE reasons. A real lending build produced `foir_above_cap`,
  `income_not_corroborated`, `bureau_adverse` — every one a reason to reject the
  LOAN. An officer who was *approving* a loan the agent had rejected was shown a
  list arguing entirely for the decision being overturned. There was no correct
  option.
* It actively suppressed learning. A clause is retrieved iff
  `scope_facets ⊆ case_facets`, and scope is the cluster's facet INTERSECTION —
  so whatever partitions the clusters decides the SCOPE of every judgement.
  Clustering partitioned on the code, which let a hand-picked *why* choose a
  *where*: two corrections about identical cases never got compared when the
  officers happened to pick different labels, each cluster fell below the
  minimum size, and both were silently dropped.

What carries the meaning instead, all of it derived or written by the officer:

| Question | Answered by | Source |
|---|---|---|
| what KIND of case is this | `case_facets` | derived from columns (§1) |
| what did the officer change | `contested_fields` | derived from the override delta |
| what is the lesson | the officer's own sentences | typed, min 10 words |

So: declare **facets** carefully — they are the category a judgement is scoped
to and injected against. Declare no reason codes.

---

## 2. Shape

```jsonc
"case_signature": {
  "version": 1,
  "facets": [ … ],          // 1–24
  "learning": { "promotion_min_officers": 3, "clause_budget_words": 1000 }
  // no "reason_codes" — deprecated, see §1b
}
```

### Facet kinds

| kind | Needs | Emits |
|---|---|---|
| `enum` | `from_column` + `values` (**required**) | `family:<value>` |
| `band` | `from_column` (numeric) + `edges` | `family:lt_1000`, `family:1000_25000`, `family:gte_25000` |
| `presence` | `from_column` | `family:present` / `family:absent` |
| `age_band` | `from_columns: [start, end]` + `edges` (days) | same banding as `band` |
| `signal` | `signal_id` from the platform set | `family:fired` / `family:clear` |

`vertical`, `sub_vertical` and `country` are emitted automatically from the
dataset ontology — never author them.

`values` is **required** on `enum` and the publish gate rejects it without them.
An undeclared value at run time becomes `family:__unknown`, which is counted and
surfaced as ontology drift. Omitting `values` would silently accept anything and
make drift invisible — that is why it is an error, not a warning.

---

## 3. Worked example — motor claim approval

```jsonc
"case_signature": {
  "version": 1,
  "facets": [
    { "family": "loss_type",    "kind": "enum",     "from_column": "loss_type",
      "values": ["collision","theft","fire","flood","windshield","vandalism"] },
    { "family": "policy_class", "kind": "enum",     "from_column": "policy_class",
      "values": ["personal","commercial_fleet"] },
    { "family": "amount_band",  "kind": "band",     "from_column": "claim_amount",
      "edges": [1000, 25000, 100000] },
    { "family": "police_report","kind": "presence", "from_column": "police_report_no" },
    { "family": "policy_age",   "kind": "age_band",
      "from_columns": ["policy_start_date","loss_date"], "edges": [30, 180] },
    { "family": "exif",         "kind": "signal",
      "signal_id": "exif_capture_before_claim" }
  ],
  "learning": { "promotion_min_officers": 3 }
}
```

A $38k theft claim with no police report produces:

```
loss_type:theft · amount_band:25000_100000 · police_report:absent ·
policy_class:personal · policy_age:lt_30 · exif:clear
```

A judgement scoped to `[loss_type:theft, amount_band:25000_100000]` applies to
it. One scoped to `[loss_type:windshield]` cannot.

---

## 4. Platform signal ids

Only these are valid for `kind: "signal"`, and only when the app enables fraud
screening. Anything else fails the publish gate:

```
exif_capture_before_claim · exif_gps_far_from_claim · camera_model_flip
payment_ref_not_found · payment_amount_mismatch · payment_date_mismatch
payment_party_mismatch · photoset_timing_cluster · verify_ref_not_found
verify_field_mismatch · date_rule_violation · statement_chain_break
shared_identifier · identity_cardinality · resubmitted_after_rejection
```

---

## 5. Declare facets that change the DECISION, not everything you know

A **team judgement** forms when several officers make the same correction on comparable cases (a single officer's correction already works as an individual judgement, clearly labeled).
Corrections do not need identical facet sets to combine — incidental
differences (a complaint arriving by `app` vs `care_line`) do not keep them
apart, and the judgement's scope comes out as what the cases genuinely SHARED,
filtered down to the facets that are actually informative.

So the question for each candidate family is not "do we know this?" but
**"would an officer's correction ever depend on it?"**:

* ✅ `category`, `amount_band`, `police_report` — a routing or approval
  judgement plausibly turns on these
* ❌ `channel`, `status` — the intake channel rarely changes *why* a routing
  was wrong, and a column that is the same for every case the app processes
  (`status:new`) carries zero information

Families that never decide anything still cost: they widen the drift surface
(every family can emit `__unknown`), pad every stored signature, and add noise
to comparability ranking. The publish gate rejects a signature above **20 000**
combined cells and warns above 5 000 — treat the warning as "you are declaring
context, not decision factors."

**Prefer wide bands and few families.** A judgement that came out too general is
visible and fixable; deleting a useless family later invalidates the scopes
built on it.

---

## 6. Publish errors (rule `CS-01`)

| code | Fix |
|---|---|
| `case_signature_unknown_column` | Column not on the bound dataset — check the name, or the facet emits `__unknown` for every case |
| `case_signature_type_mismatch` | `band` needs a numeric column; `age_band` needs date columns |
| `case_signature_unknown_signal` | `signal_id` outside the list in §4 |
| `case_signature_bad_bands` | `edges` must be strictly increasing |
| `case_signature_duplicate_family` | Two facets share a `family` name |
| `case_signature_thin_taxonomy` | Need ≥2 substantive reason codes besides `other` |
| `case_signature_cardinality` | Facet space too sparse — drop a family or widen bands (§5) |

---

## 7. Do not author

* `learning.mode` — does not exist. Clause memory is the only memory path.
* Anything under `smartapp_clauses` — judgements are formed from real
  officer corrections, never hand-written. A judgement with no evidence behind
  it is exactly what this system replaced.
* Never describe the learned layer as "rules" to the BA — RULES are the SOP,
  which stays supreme; the app learns JUDGEMENTS on top of it.
