<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# How a judgement lives

*The life story of one learned rule — from the moment an officer disagrees with
the app, to the day it stops being used.*

This is the narrative companion to `docs/clause-memory-graph-plan.md`. That
document specifies the machinery; this one follows a single judgement through
it, in order, so you can see why each part exists. Every number here is the
value in the code, not an illustration.

---

## Prologue: two kinds of knowledge

An app that makes decisions draws on two completely different things, and the
whole design turns on keeping them apart.

The first is **the rules**. Those are the organisation's SOP — written by
people, approved by a committee, fetched live on every run. The app does not
learn them, cannot change them, and never overrules them. If §4.2 says a
proposal breaching the exposure cap is declined, it is declined.

The second is **judgement**. That is what your officers know and the SOP does
not say. No clause anywhere in Acme Power's manual reads *"a theft report is
revenue protection's problem, not the line crew's"* — but every experienced
officer in that room knows it, and they know it because they have watched line
crews sit on theft reports for a fortnight.

The SOP cannot capture that, because nobody wrote it down. The app can, because
it watches what officers do.

> **A note on words.** We never call the learned layer "rules". Rules are the
> SOP and they are supreme. What officers teach is *judgement*: one officer's
> is an **individual judgement**, several officers agreeing makes a **team
> judgement**. That distinction is not decoration — it changes how the app
> presents what it knows, and it runs through every screen.

---

# Part One — Birth

## Chapter 1: An officer disagrees

Asha opens a complaint. The app has read it, routed it to the local line crew,
and proposed that as the answer.

She rejects it, and types a sentence:

> *"theft reports must go to revenue protection, not the local line crew"*

The reason is required. That is deliberate: an unexplained rejection tells the
system that it was wrong but not *how*, and there is nothing to learn from it.

Three things are recorded, and they matter in different ways:

| What | Why it is kept |
|---|---|
| Her reason, verbatim | It is the only thing a person can supply. Everything else is derived. |
| What she changed (`assigned_to`: Line Crew → Revenue Protection) | The *move*. Prose can be vague; a delta is unambiguous. |
| The case's **facets** | What kind of case this was. This decides who else the lesson applies to. |

That last one is the load-bearing part. This case's facets are
`category:theft_report` and `priority:high` — derived by code, not by the model,
from the app's declared categories. Asha never sees the word "facet" and never
picks one. She just corrected a case; the system already knew what kind of case
it was.

Nothing else happens. The app does not learn anything yet.

## Chapter 2: The wait

Asha's correction sits in a queue.

This is the first thing people find surprising, so it is worth being plain
about: **one officer disagreeing once teaches the app nothing immediately.**

Not because her view does not count — it will, shortly, and sooner than you
might expect — but because a system that rewrote its own guidance on every
click would be unusable. The earlier design did exactly that: every rejection
triggered a rewrite of a thousand-word summary, synchronously, inside the
officer's approve request. Every correction was a fresh lossy re-encoding of an
already-lossy encoding. The tenth lesson degraded the first.

So corrections accumulate, and a batch job picks them up:

- **every 15 minutes**, in the background, never in an officer's request
- when a bucket has **5 pending corrections**, or the oldest is **6 hours** old
- one leader only, so two servers cannot both learn the same thing

Meanwhile Bhavna hits the same problem on a different complaint. Then Chetan.
Three officers, three cases, three sentences that say the same thing in
different words.

## Chapter 3: The night it becomes a sentence

The batch runs. Here is what it actually does, in order.

**It sorts corrections into buckets** by app, by kind of work (a photo check and
a routing decision are not the same conversation), and only then looks for
patterns within a bucket.

**It clusters.** Two corrections join the same cluster when they are similar
*two* ways at once:

- their words overlap — **0.34** on content words, stopwords stripped. Deliberately
  lexical, not embeddings: a hard partition plus the facet gate already separates
  lessons, and adding a network call inside the batch buys very little.
- **and** their cases overlap — at least **half** the facets they could share.
  This is an overlap coefficient, not a Jaccard. That distinction fixed a real
  bug: with Jaccard, the denominator grew with every category an app declared,
  so two corrections about the *same* lesson that differed on incidental
  categories scored *lower the richer the app's vocabulary was*. Declaring more
  context must never make an app slower to learn.

Asha, Bhavna and Chetan's three corrections cluster. Good.

**It checks the cluster is worth learning from.** Two gates, and both exist
because of specific failures:

- **At least 6 distinct content words.** "ok", "see file", "as discussed" ×3
  cluster beautifully on mutual similarity and would otherwise author a
  content-free judgement, injected forever, with three officers' names on it.
- **Pattern, not person.** Text naming an individual — an honorific plus a name,
  or a long identifier — is rejected outright. The app is learning how this kind
  of case works, not who to be suspicious of.

**It works out who the lesson applies to.** This is the cleverest step and the
easiest to get wrong. All three cases were `category:theft_report` *and*
`priority:high`. Should the judgement apply only to high-priority theft
reports — or to all theft reports?

The job compares each facet against how often it appears across the app's whole
correction history. A facet is kept only if it is at least **1.3×** more common
in this cluster than at large, and only once there are **20** corrections to
compute a base rate from. If most complaints are high priority anyway, then
`priority:high` is telling us nothing and is dropped. If theft reports are a
small slice, `category:theft_report` is informative and is kept.

This is why the scope is inferred rather than asked for. Nobody has to guess
which categories matter — the evidence says.

**Then, once, it writes a sentence.** One model call, one time, at birth:

> *"Route theft reports to revenue protection, not to line crew."*

At most **40 words**. A judgement longer than that is two judgements.

And here is the invariant the whole store exists to protect: **that sentence is
never rewritten.** Later corrections can add support, sharpen the counters,
attach more provenance — they cannot touch the text. The Nth correction cannot
degrade the (N−1)th lesson, because the text was encoded once.

The judgement is also required to cite the corrections it came from. A clause
with no provenance is refused outright — that would be model-authored policy,
and this store does not accept it.

## Chapter 4: One voice, then a team

The judgement is born with a status, and the status is about *corroboration*.

**Three distinct officers** taught this one, so it is born **active** — a **team
judgement**. The app will assert it, cite it, and act on it.

Had only Asha corrected, it would have been born **candidate** — an
**individual judgement**. And this is the part people expect to be wrong and it
isn't: a candidate **is used immediately**. It goes into the prompt on the very
next matching case, labelled honestly:

> *"one officer's judgement — not yet corroborated by the team; weigh it and
> verify against the record before relying on it"*

The doctrine is explicit about why. A lone officer's experience is used and
labelled, never hidden. A one-officer branch office must still be able to learn.
Waiting for a quorum would mean the smallest teams — the ones with the least
support and the most need — learn nothing at all.

What the count buys is not *membership*, it is *authority*. Three officers make
it the team's position. One officer makes it one person's, and it says so.

The gate counts **distinct officers**, never per-officer weights. There are no
trust tiers anywhere in this system, and Chapter 10 explains why that is a
deliberate refusal rather than an omission.

---

# Part Two — Life

## Chapter 5: How it comes back

A new complaint arrives three weeks later. Code derives its facets:
`category:theft_report`, `priority:high`, `channel:care_line`.

The retrieval rule is one line, and everything else follows from it:

> **A judgement fires if and only if its scope is a subset of the case's
> facets.**

Our judgement is scoped to `category:theft_report`. The case has that, plus
more. Subset holds. It fires.

Change one thing and it does not: a billing complaint has no
`category:theft_report`, so the subset fails and the judgement stays silent. It
is not "considered and rejected" — it is never retrieved. That is what stops an
app's learned knowledge turning into noise as it accumulates: the store can grow
without bound while what reaches any given case gets *narrower*.

There is one token that can never be scoped to: `__unknown`. When a category
cannot be worked out for a case — a null in the column, a value nobody
declared — the case gets `product:__unknown` rather than a guess, and no
judgement may be scoped to it. An undecidable category stops matching rather
than matching the wrong thing.

## Chapter 6: Why not everything fires

Several judgements may match one case. The prompt has a budget — about **1000
words** — so they compete.

Specificity wins first: a judgement scoped to three categories beats one scoped
to one, because it was learned on cases more like this one. Within a tier:

```
score = 1.0 × log(1 + officers)          how many people back it
      + 1.5 × precision                   how often it has been right
      + 0.75 × recency                    how recently it was confirmed
```

Precision carries the most weight — more than headcount. A judgement three
officers taught that keeps being overruled ranks below one that two officers
taught and that keeps being right. An unproven judgement is assumed **0.8** and
recency halves every **180 days**.

Individual judgements are capped at **3** per case. A case may consult a few
uncorroborated opinions; it must never drown in twelve.

---

# Part Three — Doubt

Four different things can go wrong with a judgement, and each has its own
answer. This is the part of the system that was weakest and has been rebuilt.

## Chapter 7: The team disagrees

Divya gets a theft report where the judgement fires — and overrules it. The app
records her as a **dissenter**.

Dissent is stored, never resolved. When dissenters reach **34%** of everyone who
has weighed in, the judgement flips to **dissented**: it stops being asserted
and renders as a *disagreement notice* instead.

The asymmetry is deliberate. One supporter against one dissenter is not a
judgement either way — it is an **open question between two officers**, and
presenting it as anyone's settled view would silently pick a winner.

## Chapter 8: The SOP says otherwise

If a judgement contradicts the SOP, it is pulled from use and flagged
**sop_conflict**. The SOP wins by default, always. But a supervisor gets two
buttons, because there are two possible truths:

- **The SOP is right** → the judgement retires.
- **The officers are right** → the judgement returns to service carrying an
  acknowledgement, which is the organisation's signal that **the SOP itself is
  stale and needs updating**.

That second button is the interesting one. An app that only ever deferred to the
written rule would quietly bury the most valuable thing it knows: that the
written rule has fallen behind the work.

## Chapter 9: The numbers say it is wrong

Every time a judgement fires, that is counted. Every time an officer overrules a
case *and the model cited that judgement*, that is counted as a blame.

```
precision = 1 − blamed / fired
```

Measured only after **10 firings** — punishing a judgement on a one-case
accident would retire good ones for being new. Below **0.7**, the judgement is
withdrawn: status **underperforming**, out of use, waiting for a person.

This is the quiet workhorse of the whole design, and it is worth saying why.
Corroboration is a headcount. Three officers who share the same misconception
form a team judgement, and no amount of counting heads catches that. **The cases
do.** When the work keeps contradicting a judgement, it stops being applied —
and nobody had to outrank anybody.

For a long time this number was computed and then ignored: the floor existed in
exactly one place, a statistic nobody acted on, so a judgement officers had
overruled on 12 of 20 cases carried on firing, merely ranked slightly lower.
Measured wrongness with no consequence is the same failure as knowledge that
looks present and is not.

A withdrawn judgement can be **put back**. That matters more than it sounds:
once withdrawn it never fires, so its counters freeze and precision can never
recover on its own — without a way back, a judgement that dipped during a bad
fortnight would be dead forever. Reinstating it **resets its record**, giving it
a genuine fresh window. (Reinstate without the reset and the next batch re-parks
it from the same totals — a flap, not a decision.) The numbers that withdrew it
are kept on the history entry, so the reset is not a laundering of its past.

## Chapter 10: One person knows better

Here is the case none of the above handles.

Three officers agree on something and are **wrong**. Not maliciously — they are
new, they share an assumption, they trained together. The judgement is active
and being applied. And the one person in the room with twenty years of
experience knows it is wrong.

Under corroboration alone, watch what happens. She dissents. That is one
dissenter against three supporters: 1 ÷ 4 = **0.25**, under the 0.34 threshold.
**Nothing happens.** She has to go and find a second person to agree with her
before her objection has any effect at all — while the three juniors needed
nobody's agreement to set the policy in the first place.

That inversion is the problem. The obvious fix is to weight her vote, and **we
refuse to do that**, on three grounds:

- A credit file that says *"this carried more weight because Rakesh is senior"*
  is not defensible to a regulator.
- Seniority is not correctness. She is usually right. Usually is not always.
- A weight is a permanent political artifact and it is gameable.

So she gets a different kind of lever: she can **challenge** the judgement.

A challenge does not outvote anyone. It **stops** the judgement — out of use
immediately — and forces a named human to decide. It requires a reason, because
whoever adjudicates is being asked to choose between two officers and cannot do
that from a flag. And it is fully attributed: who raised it, when, why, in their
own words, with every transition written to the judgement's history.

The adjudication has three outcomes:

| | What it means |
|---|---|
| **Uphold** | The challenger was right; the judgement retires. The corrections that taught it stay, so a corrected version can form. |
| **Dismiss** | The objection is overruled; the judgement goes back to exactly the state the challenge interrupted. |
| **Withdraw** | The challenger taking their own objection back — the only resolution they may make themselves. |

Two rules make this a control rather than a loophole, and both were learned the
hard way when the first version of this feature was reviewed:

**The challenger cannot adjudicate their own challenge.** Without that, one
person could park a judgement three officers taught and retire it alone, with no
second name anywhere — the exact opposite of "forces a named human to decide".
They can still withdraw, so a one-admin organisation is never stuck; a
withdrawal simply is not dressed up as an adjudication.

**Dismiss restores, it never promotes.** The first version re-derived the tier
from the officer count, which meant challenge-then-dismiss would return *any*
parked judgement to active — lifting an administrator's hold, undoing a
withdrawal the evidence had earned, clearing a dissent. Challenging is now
possible only against something actually in service, and dismissing returns it
precisely to what it was.

---

# Part Four — Endings

## Chapter 11: The app changes shape

Someone rebuilds the app and renames a category — `income_proof` becomes
`income_proof_type`. Every judgement scoped to the old name can now never match
any case again. They are still sitting there marked active. They fire on
nothing.

This happened in production, and it was invisible: the app went on reporting
knowledge it could no longer apply.

Publish now reconciles. A rename **declared** in the category's `aliases`
migrates the scope in place and the judgement keeps working. A category that
simply vanished moves its judgements to **orphaned** — out of use, so the app
stops claiming knowledge it cannot apply. Visible, reversible, and never silent.

## Chapter 12: Retired, superseded, held

The remaining endings are ordinary:

- **Retired** — a verdict. Someone decided it is wrong. Never edited into
  something else: a judgement's text is provenanced to specific corrections, so
  rewriting it would leave a sentence claiming evidence that does not say that.
  Retire and let a new one form.
- **Superseded** — a more general judgement absorbed it.
- **Quarantined** — a *hold*, not a verdict. "That officer was dismissed — pull
  what they taught, pending review." Reversible, evidence untouched.

**Nothing is ever deleted.** Every ending keeps the corrections that produced
the judgement, which is what allows a corrected version to form from the same
evidence.

---

# Part Five — The Memory screen

Everything above is visible in one place: **App Memory**, on the admin section
of the home panel.

### Judgements it has learned

Each judgement shows its sentence, its status in plain language, and the
categories it applies to as chips — *"Applies to every case of this kind"* when
it has no scope.

The status is written for a person, not a machine: *"individual — one officer,
awaiting corroboration"*, *"conflicts with SOP — needs your call"*, *"stopped —
someone disagreed, waiting on a decision"*, *"withdrawn — your team kept
overruling it"*. Anything not in use is amber.

### Why it learned this

Tap a judgement and it opens the corrections behind it — each officer's own
words, verbatim, with their name and the date. Not a summary. The actual
sentences three people typed.

This is the screen's most important function. Nobody has to trust the
judgement: they can read what taught it and decide for themselves.

### What one officer taught

Every officer's name in that list is a link. Tap it and you get **every
judgement that person helped teach** in this app.

This is the drill for *"she left"*, *"he turned out to be wrong about this whole
class of case"*, *"that was the intern's first week"*. Each row opens that
judgement's own controls, so you act on them one at a time with a reason —
rather than a bulk purge that erases why.

### The controls

| Action | When you would use it |
|---|---|
| **Challenge this** | You believe it is wrong. Stops it now, forces a decision. Reason required. |
| **Retire** | A verdict: it should not be used again. Two taps. |
| **Quarantine** | A hold while you find out. Reversible. |
| **Put it back and watch it again** | For a judgement the numbers withdrew, when you think the numbers misled. Resets its record. |
| **SOP resolution** | Two taps: the SOP is right, or the SOP is stale. |

Every one of them writes the actor, the time and the cause into the judgement's
history. There is no way to change what the app knows without leaving your name
on it.

### The other two tabs

**Past decisions** — every photo and document the app has examined, what it
suggested, what your team decided. Read-only. A row can be excluded from being
used as an example, but the record itself is never edited or deleted.

**How it's doing** — override rate, how often decisions turned out right, how
much is handled automatically, and an inventory of everything learned.

---

# Epilogue: four things this system refuses to do

Most of the design is in what it will not do.

**It will not rewrite a judgement.** Written once, at birth, from the
corrections that produced it. Everything after that touches counters and
provenance. This is the entire defence against generation loss.

**It will not weight officers.** No trust tiers, no seniority scores. Experience
gets a lever — the challenge — not a multiplier. Nobody's standing is stored
anywhere.

**It will not learn about people.** Text naming an individual is refused at
authoring. The app learns how a kind of case works.

**It will not let knowledge look present when it is not.** A judgement that
cannot match gets orphaned. One that keeps being wrong gets withdrawn. One
scoped to `__unknown` cannot exist. The failure this design fears most is not
being wrong — it is *appearing to know something you no longer know*.

---

## Appendix A: statuses

| Status | In use? | Meaning |
|---|---|---|
| `candidate` | yes, labelled | One officer's judgement, not yet corroborated |
| `active` | yes | Team judgement — corroborated by 3+ distinct officers |
| `dissented` | as a notice | Officers disagree; an open question, not a verdict |
| `challenged` | no | Someone stopped it; waiting on a named adjudicator |
| `underperforming` | no | The cases contradict it; withdrawn on evidence |
| `sop_conflict` | no | Contradicts the SOP; supervisor must resolve |
| `quarantined` | no | An administrator's hold. Reversible |
| `orphaned` | no | Scoped to a category the app no longer produces |
| `retired` | no | A verdict. Evidence kept, judgement not used |
| `superseded` | no | A more general judgement absorbed it |

## Appendix B: the numbers

| Setting | Value | What it governs |
|---|---|---|
| Consolidation interval | 15 min | How often corrections are folded |
| Trigger | 5 pending, or 6 h old | When a bucket is processed |
| Cluster: word similarity | 0.34 | Do two corrections say the same thing |
| Cluster: category overlap | 0.5 | Are they about the same kind of case |
| Minimum cluster | 2 corrections | Below this it is not yet a lesson |
| Minimum substance | 6 content words | Blocks "ok" ×3 becoming a judgement |
| Scope lift | 1.3× over base rate | Which categories are informative |
| Base-rate sample | 20 corrections | Before base rates are trusted |
| Promotion | 3 distinct officers | Individual → team judgement |
| Judgement length | 40 words max | One sentence, one lesson |
| Dissent threshold | 34% | When disagreement suspends it |
| Precision floor | 0.7, after 10 firings | When the evidence withdraws it |
| Injection budget | ~1000 words | How much reaches one case |
| Individual cap | 3 per case | Uncorroborated opinions per prompt |

*Every value is per-app configurable through the app's learning settings, except
where noted. The defaults are what ships.*
