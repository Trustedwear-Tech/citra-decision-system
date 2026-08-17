# Plan: work in one repo, keep private material in `private/`

Status: plan. Nothing moved yet. Written 2026-08-17.

Goal: stop maintaining two trees. Work only in `citra-decision-system`, keep
the private material in a single gitignored `private/` folder, and back that
folder up to `Citra-AI`.

---

## Why

The two-tree model failed repeatedly in one session, and every failure was a
variant of the same thing — a value that is correct in one tree and wrong in
the other, moved mechanically:

- `make_mcp.py` had the docker network hardcoded. A fix correct for the public
  quickstart (`citra-network`) was copied into the private tree, which runs on
  `citra-ai-net`, and broke MCP generation there.
- `seed-demo.sh` had the acme-bank Postgres port hardcoded. Same shape: 15444
  public, 5444 private.
- The licence stamp made the sync report 473 files pending, 458 of them
  unchanged. Applied blindly it would have rewritten public content from false
  positives.
- `runner/outbox.py` was missing from the public tree while `adapter.py`
  imported it, so no builder pod could start. `sync_public.py` reconciles only
  the intersection of tracked files, so it could not see this.
- The live `.env` was committed into the PUBLIC repo twice. Push protection
  caught both.

None of these are bugs in the sync tool that a better sync tool fixes. They are
what you get from keeping two copies of the same thing.

## The shape

One working tree: `citra-decision-system`. Everything private lives under
`private/`, mirroring the real directory structure:

```
private/
  scripts/            deployment .ps1, Vault bootstrap, AWS/SSM
  infrastructure/aws/ IAM policies, deploy workflows
  Citra-UI/           the marketing landing page, if it stays private
  docs/               internal design docs
```

`.gitignore` gains one line: `private/`.

`Citra-AI` stops being a parallel tree and becomes the backup of `private/`.
One script, one direction, no header rewriting, no "which value wins".

## What moves, measured

Of the 181 files currently private-only:

| | Files | |
|---|---|---|
| Movable into `private/` | 135 | AWS, migration scripts, deploy `.ps1`, internal docs, marketing components, `embed-test/`, `config/` |
| `.env*` | 34 | Path-locked — compose reads them where they are. Already gitignored |
| `.vscode/`, `.claude/` | 9 | Path-locked — tooling needs them at root. Standard ignore |
| keystores, `sha1.txt` | 3 | Path-locked — gradle references them |

The 46 path-locked files contain **no shared code and no docs**. They are
config, editor settings and signing material, so there is nothing left for two
trees to disagree about. That is what makes this work rather than merely tidy.

### Firebase hosting — a REMOVAL, not a move

Three more files go to `private/`, and these are different in kind from the 135
above: they are tracked in **both** repos today, so this deletes them from the
public tree rather than relocating something already private.

| file | why |
|---|---|
| `Citra-UI/.firebaserc` | names the live projects — `citra-ai-6b291` and `citra-ai-test-6b291` |
| `Citra-UI/firebase.json` | hosting targets, redirects, rewrite rules for those sites |
| `Citra-UI/deploy-simple.ps1` | the deploy script itself |

Firebase hosting is how citra-ai.com is published. It is our deployment, not
something a self-hoster runs, and `.firebaserc` is live topology: the project
ids identify real Firebase projects and belong with the AWS material, not in a
source-available repo.

Nothing in the public tree imports these — they are invoked by hand — so
removal does not affect the build. Check `Citra-UI/package.json` scripts for a
`deploy` entry referencing them before deleting; if one exists it should go too.

This also means the public `.gitignore` needs the three paths listed
explicitly, not just `private/`: they are currently tracked, so `git rm
--cached` plus an ignore rule, or they come straight back on the next `git add`.

`.gitignore` ends up about five conventional lines:

```
private/
.env
.env.*
.vscode/
.claude/
```

**Do not use glob patterns for the private set.** Tested: `*.ps1` and `.env*`
would also hide 22 files the public repo needs, including `.env.example`,
`Citra-UI/deploy-simple.ps1` and `Citra-Service/scripts/update_milvus_schema.ps1`.
Some deployment is public — local deployment is shared. A single `private/`
directory avoids this entirely, which is the main argument for it over a
curated ignore list.

## The landing page

Decision: **one landing page, not two.** Today there are two —
`IntroScreen.js` (5,766 lines, private, the citra-ai.com marketing site) and
`components/LandingScreen.js` (the OSS stand-in) — plus two shim files added on
2026-08-17 so the public tree could build at all.

The reason for the split was that IntroScreen depends on externally-hosted
media. Confirmed by reading it: it imports `Video` from `expo-av` and embeds a
`youtube-nocookie.com` iframe (`YouTubePlayer`, around line 1835). On a
self-hosted or air-gapped install that fetch fails and the hero is empty.

### Decision: the FILE is the switch, not an env var

An env flag selecting the landing page at runtime was considered and rejected
on a hard constraint: **a bundler resolves imports at build time, an env var is
read at runtime.** With the marketing page under a gitignored `private/`, the
public build dies at

    Unable to resolve module ./IntroScreen from /app/MainApp.js

before the flag is ever read — the exact failure fixed on 2026-08-17. Metro
will not resolve a variable import path either, so a guarded dynamic require
does not rescue it.

Chosen instead: `Citra-UI/IntroScreen.js` and `MobileIntroScreen.js` in the
public tree are thin shims that render `components/LandingScreen`. They already
exist. The private build overlays those two files from
`private/Citra-UI/` before bundling. `MainApp.js` is untouched and identical in
both, both trees build standalone, and the overlay copy IS the toggle — no new
mechanism, no env var.

That also preserves the property the clean-room test exists to protect: the
public tree builds on its own, with no private file present.

Consequences, both good:

- The marketing page stays private, so the third-party endorsement names and
  the usage counters (false on a fresh install) are not published.
- `components/intro/` (StoryCarousel, ProvenImpact, WhyCitra, GetStarted) stays
  private too — it is imported only by the real IntroScreen.

### The video hero — do this regardless of which page ships

`IntroScreen.js` imports `Video` from `expo-av` AND embeds a
`youtube-nocookie.com` iframe (`YouTubePlayer`, around line 1835). On an
air-gapped or firewalled install that fetch fails and the hero renders empty.

Handle it defensively: on failure, collapse the hero and promote the next
section into its place, so the page reads as designed rather than broken.
A cross-origin iframe does not reliably fire `onError`, so this needs BOTH an
error handler and a readiness timeout — an error handler alone will not fire on
the most common failure, which is a request that simply never returns.

## Deletions

Marketing collateral and commercial material is dropped rather than moved:
investor scripts (`Citra-AI-Investor-Script.docx/.pdf`), the executive-overview
deck, website thumbnails. Confirm the list before deleting — some of it exists
nowhere else.

## Credentials to deal with separately

These are in the private tree today and must not simply move into a folder
inside a public checkout:

- `Citra-Service/memory-assist-477012-d1a6fff27cec.json` — looks like a Google
  Cloud service-account key. **Rotate it.** It is in git history either way.
- `Citra-UI/android/app/debug.keystore`, `keystore-info.txt`, `sha1.txt` —
  Android signing material. Needed at their paths for builds; keep gitignored,
  do not relocate.

## Order

1. Fix the stale proprietary headers — **done**, 2026-08-17. 16 files converted.
   Independent of this migration but the files were inconsistent.
2. Agree the deletion list.
3. Unify the landing page and verify the no-video path in a browser.
4. Create `private/`, move the 135, add the ignore rules.
5. Point the backup script at `private/` and retire `sync_public.py`, the
   pre-push sync hook, and the `generate_*_repo.py` scripts.
6. Add a pre-commit check that fails on any tracked file outside the agreed
   public set — after this, private files sit inside the public checkout, and
   push protection catches secrets but not AWS topology or commercial docs.

Step 6 is not optional. The migration trades one failure mode (a private value
syncs into public) for another (a new private file is not under `private/` and
gets committed). The second is quieter, so it needs a mechanical check rather
than care.
