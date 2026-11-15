# Licensing implementation guide

How the pieces fit together: SPDX conventions, the CLA bot and its workflow,
the per-file ledger and manifests, the auditor, the configuration, purchased
assets, consent issues and the private mirror. The policy itself is in `legal/`.

---

## SPDX conventions

The project adopts [SPDX license identifiers](https://spdx.dev/learn/handling-license-info/)
as the standard way to express licensing in machine-readable form. This applies to
directory-level notices, per-file headers, workflow outputs, and documentation.

### SPDX identifier mapping

| Current text | SPDX identifier | Where used |
|---|---|---|
| GPL v2+ / GPLv2-or-later / "GNU GPL, v2 or later" | `GPL-2.0-or-later` | Code default, CONTRIBUTING.md, LICENSE.md |
| GPL v2 (explicit, no "or later") | `GPL-2.0-only` | Beherith headers, Raptors/License.txt |
| Public Domain | `LicenseRef-Public-Domain` | `include/walk.lua`, `Units/armdecom.lua` |
| BAR Project License (CLA) | `LicenseRef-BAR-CLA-1.0` | Art/audio CLA (custom, not in SPDX registry) |
| CC-BY-SA 4.0 | `CC-BY-SA-4.0` | Art opt-in license |
| MIT | `MIT` | Optional code license (GPLv2-compatible) |

> **Note:** The BAR CLA is not a standard open-source license and has no entry in the
> SPDX License List. The `LicenseRef-` prefix is the SPDX-standard mechanism for
> project-specific or proprietary license identifiers.

### Per-file header format

For text-based source files where a header can be added:

```
// SPDX-License-Identifier: GPL-2.0-or-later
// SPDX-FileCopyrightText: 2024 Beyond All Reason contributors
```

Comment style varies by file type:
- `.lua` / `.h` / `.glsl` / `.frag` / `.vert` / `.py`: `-- SPDX-…` (Lua) or `// SPDX-…` (C-style/Python)
- `.bos`: `; SPDX-License-Identifier: GPL-2.0-or-later` (BOS uses `;` comments)
- `.ahk`: `; SPDX-License-Identifier: GPL-2.0-or-later`

### Directory-level SPDX

`LICENSE_SCRIPTS.txt` now includes the SPDX identifier for the directory default:
```
SPDX-License-Identifier: GPL-2.0-or-later
```

---

## Operational reference: updating the CLA workflow

### How the bot works (current implementation)

The workflow runs in the calling repository: `github.*` is the caller's context,
`.github/licensing.json` is read from the caller's checkout.

1. **PR opened/updated with art files** → `detect-assets` job identifies art/audio
   files by extension and directory. `cla-check` injects a `<!-- BAR-LICENSE-SELECTION -->`
   section into the contributor's own PR description, then posts a pointer comment.
   The section has one collapsed block per file with the license boxes and the
   proxy box. With several files it starts with an **All files** block: a box
   ticked there applies to every file whose own boxes are empty. An untouched
   section in the earlier layout is replaced on the next push.

2. **Contributor ticks boxes** → nothing fires. Boxes are read only on command.

3. **Contributor posts `/confirm-license`** → `process-comment` job reads the PR body
   between section markers. Validates: commenter is PR author, exactly one license
   per file (its own box, else the All files box), ≥ 10 min since last confirmation. On success: appends per-contributor
   intent records to `cla-signatures.json` with `status: "intent"`, updates the bot
   comment, sets commit status green. **No file-level assignment is written yet.**

3a. **Proxy submission (someone else's artwork)** → if the submitter also ticks
    "I am submitting this artwork on behalf of another person" and fills in the
    artist's GitHub handle, `/confirm-license` records the intent with
    `proxy_status: "pending-author-confirmation"` and tags the artist. The artist
    then replies `/confirm-authorship` to confirm, which flips
    `proxy_status` to `"confirmed"`. The records remain `status: "intent"` until
    merge.

4. **Want to change choice?** → edit boxes, post `/confirm-license` again after the
   rate-limit window. Old intent entry is removed and replaced.

5. **PR closed without merge** → `cleanup-on-close` job removes **intent** signatures
   for the author (or proxy submitter) and strips license labels. Confirmed records
   from previously merged PRs are never deleted.

6. **PR merged** → `confirm-on-merge` job (Job 7) performs three actions in one
   commit:
   1. Applies any **file renames** detected via the GitHub API: rewrites the `file`
      field in both ledgers from `previous_filename` to the new path (license,
      contributor, and source PR are preserved — renames are **never** re-licensing
      events).
   2. Promotes all `status: "intent"` records for the PR to `status: "confirmed"`
      (with `confirmed_at`) and appends authoritative per-file entries to
      `.github/file-license-assignments.json`.
   3. Regenerates `LICENSE_MANIFEST.md` for every affected top-level art directory
      (including both source and destination when files move between art
      directories).

7. **Contributor already signed in a previous PR** → detected from
   `cla-signatures.json` at step 1; no injection needed, status set green immediately.

### Comment commands supported

| Command | Who can use | Effect |
|---|---|---|
| `/confirm-license` | PR author only | Reads checkbox state, records license intent (and optional proxy authorship declaration) for files the PR adds |
| `/confirm-authorship` | Tagged proxy artist only | Confirms files are theirs and approves the chosen license |
| `I replaced these files from scratch` | PR author only | Swaps `needs-licensing-review` → `asset: replaced` label |

Edits and moves of assets that already exist in the repository never prompt for a
license. The bot lists them as "existing assets edited, license unchanged", with the
recorded license, the author whose consent is still pending, or "no record yet".

### Labels applied by the bot

| Label | When |
|---|---|
| `license: BAR-CLA`, `license: CC-BY-SA-4.0`, … | One label per license chosen in the PR; names come from `licenses.<id>.label` in `.github/licensing.json` |
| `needs-licensing-review` | Modified existing art files detected, no declaration yet |
| `asset: replaced` | Contributor declared full scratch replacement |

Labels are reconciled on every `/confirm-license`: labels for licenses no longer chosen are removed.

### Central workflow and the stub

`cla-check.yml` in this repository is a reusable workflow (`on: workflow_call`).
A repository that wants the bot keeps a stub, `cla-check-stub.yml` here, copied
to its own `.github/workflows/cla-check.yml` with `uses:` pointing at
`beyond-all-reason/licensing/.github/workflows/cla-check.yml@main`
(a tag or commit SHA pins a version). The stub owns the triggers, grants the job
`contents`, `pull-requests`, `statuses` and `issues` write scopes, and passes
`CLA_BOT_TOKEN` and `CLA_PUSH_SSH_KEY`; each job of the called workflow narrows
the scopes to what it needs. Jobs fetch `licensing.js` from this repository at `job.workflow_sha`, the
commit the stub pinned, into `.licensing/` next to the caller's checkout, so the
scripts and the workflow always match and consumer repositories carry no copy.

A consumer repository keeps: the stub, `.github/licensing.json`,
`.github/contributors.json`, the two ledgers, the manifests and `REUSE.toml`.
Public repositories can only call a public reusable workflow, so this repository
must be public before the game repository switches to the stub. The stub here
calls the local file, which lets a PR in this repository exercise the bot.
`tools/check_workflow.py` also checks that every job of a `workflow_call` workflow
has a permissions block and that every secret it reads is declared.

The four jobs that commit to the default branch share one concurrency group
with `queue: max`, so they run one at a time and none is dropped. They check
out only `.github/`, the manifests and the flags file, which keeps a run to
seconds on a large repository.

### Secrets

- **`CLA_BOT_TOKEN`** (optional): token of the bot identity (GitHub App installation
  token or a PAT with `contents: write`), used for the bot's commits to the ledgers.
  The stub passes it explicitly; a repository without the secret passes an empty
  value and the workflow falls back to `github.token`, which cannot push to a
  protected branch and whose commits trigger no other workflows. An expired token
  fails every commit job at checkout with "could not read Username for
  'https://github.com'": renew it or delete the secret.
- **`CLA_PUSH_SSH_KEY`** (optional): private half of a write deploy key. When set,
  the four jobs that commit to the default branch check out and push over SSH
  with it. For a branch whose rules let only deploy keys push without a pull
  request. Pushes made with a deploy key trigger the repository's other
  workflows.

---

## Per-file license tracking

The bot maintains two separate ledger files with different roles:

| File | Role | Written by |
|---|---|---|
| `.github/cla-signatures.json` | Working file: per-contributor intent and confirmed signatures | `/confirm-license`, `/confirm-authorship`, `confirm-on-merge`, `cleanup-on-close` |
| `.github/file-license-assignments.json` | Authoritative per-file ledger | **Only** `confirm-on-merge` (i.e. on PR merge) |

### `file-license-assignments.json` format

Flat JSON array, one entry per merged file:

```json
[
  {
    "file": "music/epicbattle.ogg",
    "license": "LicenseRef-BAR-CLA-1.0",
    "contributor": "artist123",
    "pr": 4201,
    "date": "2026-05-10"
  },
  {
    "file": "unitpics/armcom.png",
    "license": "CC-BY-SA-4.0",
    "contributor": "artist456",
    "submitted_by": "devmaintainer",
    "pr": 4202,
    "date": "2026-05-11"
  }
]
```

- `contributor` is the credited author (the artist; for proxy submissions, **not**
  the GitHub user who opened the PR), written as the display name
  `.github/contributors.json` maps to their login, else the login itself. The
  audit ledger uses the same names, so one person keeps one spelling.
- `submitted_by` appears only when the file was contributed via the proxy flow.
- **First writer wins**: if a subsequent PR re-touches a file that already has an
  assignment, the original entry is preserved. Reassignment requires a maintainer
  edit (or the `asset: replaced` flow with a manual ledger update).

### Per-folder `LICENSE_MANIFEST.md` manifests

On every PR merge, `confirm-on-merge` regenerates a `LICENSE_MANIFEST.md` inside each
affected top-level art directory. The file lists every recorded asset (recursive)
under that directory with license, contributor, PR, and date — sourced from
`file-license-assignments.json`. Headed by an `<!-- AUTO-GENERATED -->` notice; do
not edit by hand.

Covered top-level directories: `anims/`, `bitmaps/`, `fonts/`, `icons/`,
`modelmaterials/`, `modelmaterials_gl4/`, `music/`, `objects3d/`, `sidepics/`,
`sounds/`, `unitpics/`, `unittextures/`.

Files not yet processed through the CLA bot do not appear in the manifest — they
fall under the project policy in [legal/POLICY_PROJECT_LICENSE.md](legal/POLICY_PROJECT_LICENSE.md).

### File protection

The authoritative files in the CLA system are owned by the licensing maintainer team
via [.github/CODEOWNERS](.github/CODEOWNERS). All listed paths require review from
that team before a manual change can land. The CLA bot pushes via `CLA_BOT_TOKEN`;
branch protection must be configured on `main` to **allow that bot account to bypass
the code-owner review requirement** so automated workflow commits (signatures,
assignments, regenerated manifests) can land without human approval.

Protected paths (see [.github/CODEOWNERS](.github/CODEOWNERS) for the canonical list):

- `.github/cla-signatures.json`
- `.github/file-license-assignments.json`
- `<art-dir>/LICENSE_MANIFEST.md` for every top-level art directory
- `.github/workflows/cla-check.yml`, `.github/scripts/`, `.github/licensing.json`
- `license_comments_flags.json`, `.github/CODEOWNERS`
- `legal/`, `LICENSES/`

**One-time setup on the destination repo** (BAR master, post-transfer):

1. Create the licensing maintainer team and update the `@<owner>/licensing-maintainers`
   handle throughout `.github/CODEOWNERS`.
2. In repo Settings → Branches → branch protection for `main`:
   - Enable "Require a pull request before merging" → "Require review from Code Owners".
   - Under "Allow specified actors to bypass required pull requests", add the bot
     account that owns `CLA_BOT_TOKEN`.
3. Verify by attempting a manual edit to `.github/cla-signatures.json` from a non-team
   account — the PR should be blocked until a code owner approves it.

---

## License Auditor — bulk bookkeeping tool

`tools/license_auditor/` is a PyQt6 desktop app for manually auditing the
~13 000 existing art files in the BAR repository and filling in license records
for files that predate the CLA bot (status `Gap`).  It reads directly from a
local git clone and writes back to the same JSON ledgers the bot uses.

### Install and run

```bat
# Windows — double-click or run from a terminal:
cd c:\path\to\BAR
python -m pip install PyQt6

cd c:\BAR-Tools\BAR-Licencing-implementation\tools
python -m license_auditor C:\path\to\BAR
```

Or use the provided launcher:

```bat
tools\run_auditor.bat C:\path\to\BAR
```

If no path is passed, the app opens a directory picker on startup.

Requires Python 3.10+ and PyQt6 (`pip install PyQt6`).

### UI layout

```
┌────────────────┬──────────────────────────────────────────────────────┐
│ Toolbar: Repo  │  Status filter ▾  Search: _______________            │
├────────────────┼──────────────────────────────────────────────────────┤
│ All art files  │  File                 Status    License  Contributor │
│  anims/        │  anims/walk_arm.bvh   Gap                            │
│  bitmaps/      │  anims/idle_cor.bvh   Confirmed CC-BY-SA-4.0 artist1 │
│  objects3d/    │  bitmaps/explosion.… Intent    LicenseRef-BAR-CLA…   │
│  unitpics/     │  …                                                   │
│  …             │                                                      │
├────────────────┴──────────────────────────────────────────────────────┤
│ 0 selected    [Edit Selected…]  [Save Changes]  [Commit && Push…]    │
└────────────────────────────────────────────────────────────────────────┘
```

- **Left pane** — top-level art directories with per-dir file counts and gap
  counts. Gap directories are highlighted red. Click to filter the right pane.
- **Right pane** — virtual table (handles 13k+ rows instantly). Columns:
  `File`, `Status`, `License`, `Contributor`, `Submitter`, `PR`, `Date`.
  Status cells are colour-coded: red = Gap, orange = Intent, green = Confirmed.
  Click a column header to sort. Bold rows have unsaved changes.
- **Status filter** — dropdown to show All / Gap / Intent / Confirmed only.
- **Search box** — substring filter on the file path (live, case-insensitive).

### Editing workflow

1. Select one or many rows (Shift-click, Ctrl-click, or Ctrl+A within the
   filtered view).
2. Click **Edit Selected…** (Ctrl+E) or right-click → **Edit N file(s)…**
3. In the dialog: choose a license, enter a contributor GitHub handle (without
   `@`), set status to `confirmed`. Click OK.
4. Changed rows go bold — changes are only in memory so far.
5. **Save Changes** (Ctrl+S) writes `file-license-assignments.json` to disk.
   Incomplete confirmed records (missing license or contributor) are rejected.
6. **Commit && Push…** saves, shows a preview of dirty records, lets you edit
   the commit message, and runs `git add / git commit / git push`. Uses
   `BAR CLA Bot <cla-bot@beyondallreason.info>` as the committer identity so
   the commit matches the bot's identity in the workflow history.

Right-click also has a **Quick assign license** submenu for single-field changes
without opening the full dialog.

### What the tool writes

| Action | File written |
|---|---|
| Save / Commit confirmed records | `.github/file-license-assignments.json` |
| Save / Commit gap (cleared) records | Removes entry from `file-license-assignments.json` |

The tool does **not** touch `cla-signatures.json` — that file is owned by the
bot workflow. Intent records shown in the table are read-only in the auditor;
they will be promoted to confirmed by the bot when their PR merges.

### Re-generating manifests after a bulk edit

The auditor rewrites every affected `LICENSE_MANIFEST.md` on each save. To
regenerate from the command line, or after editing the ledger by hand:

```bash
python tools/ledger.py manifests <path-to-repo>
```

### Ledger CLI

`tools/ledger.py` maintains a repo's ledgers without the GUI:

| Command | Purpose |
|---|---|
| `stats <repo>` | Coverage per art directory, licenses, awaiting authors, flags |
| `lint <repo>` | Duplicate or missing paths, non-assets, unknown licenses, rows without contributor, spelling variants, rows crediting a login that `contributors.json` names differently |
| `rebase <repo> --old <ref> --new <ref> [--apply] [--drop-missing]` | Follow renames and deletions between two commits (after merging upstream), then regenerate manifests |
| `manifests <repo>` | Rewrite every manifest from the ledger |
| `consent-body <repo> --contributor NAME [--github LOGIN] [--out FILE]` | Body for a consent issue: instructions plus the contributor's awaiting files grouped by folder |
| `apply-consent <repo> --contributor NAME --license ID [--ref REF] [--source SRC] [--apply]` | Set one license on a contributor's awaiting rows, as Job 8 does on an issue; use it to record evidence or to replicate an answer into the private ledger |
| `move <src> <dst> [--flag F]... [--license L]... [--list FILE] [--to-license L] [--keep-files] [--moved-from SLUG] [--apply]` | Move rows, flags and files to another repository; store flags become the store license |
| `reuse <repo> [--out FILE]` | Write `REUSE.toml` from the ledger: GPL default for code, one override per licence and contributor, awaiting rows with copyright only; names LICENSES texts no row uses |

`tools/spdx_sweep.py <repo> [--apply] [--set GLOB=ID[:COPYRIGHT]]` adds `SPDX-License-Identifier`
(and a copyright line where the header has none) to code files from the licence their header
already states; files with no statement get `GPL-2.0-or-later`, anything ambiguous (GPL v3,
dual or mixed statements, copyright without a grant, unparsable GetInfo values) is left alone and
listed in the `--out` report for its author. `scripts/`, `.github/` and the submodule are skipped.
Run it once per repository, then add the commit to `.git-blame-ignore-revs`.

Two more scripts read a repository without changing it: `tools/usage_scan.py <repo>` lists flagged
files nothing references (text sources, s3o texture names, listed directories, unit names,
cursors) and `tools/code_license_audit.py <repo>` classifies the licence statements in code
headers. Both write a markdown report with `--out`.

## Configuration: `.github/licensing.json`

One file holds every rule the bot and the auditor share: art directories (nested
ones like `luaui/images` allowed), art and code extensions, document and notice
patterns, the license catalogue with PR labels, the two licenses offered in PRs,
ledger paths, bot identity, status-check name and the repository's `distribution`
(`public`, or `release-only` for the private asset repository). A catalogue entry
may carry `release_only` (only recordable in a release-only repository), `store`
(id written into `sources`), `flag` (the review flag it replaces) and
`requires_rights_holder`. The workflow loads it through
`.github/scripts/licensing.js`; the auditor and `tools/ledger.py` load it through
`license_auditor.data.load_config`. A repo without the file gets the built-in
defaults.

To add an art directory, append it to `art_dirs`. To offer a third license in PRs,
add it to `licenses` (with `name`, `label`, `blurb`, `doc`) and to `pr_choices`.

`.github/scripts/licensing.test.js` checks the library; run it with a repo path to
also verify that the JS manifest renderer matches the auditor byte for byte:

```bash
node .github/scripts/licensing.test.js /path/to/BAR
python tools/check_workflow.py .github/workflows/cla-check.yml
```

## Purchased and release-only assets

Store material (Envato, Sketchfab, Blender Market, GameTextures, Unity, ZapSplat,
Pixabay) may ship inside the game archive but not as files in a public
repository. Such rows carry a `LicenseRef-<store>` identifier from the catalogue
and three extra fields:

```json
{
  "file": "sounds/weapons/evil_metal_scrape_3.wav",
  "license": "LicenseRef-Envato-Elements",
  "contributor": "icexuick",
  "basis": "purchased",
  "distribution": "release-only",
  "sources": [{ "store": "envato-elements", "item": "ITEM-ID", "licence_code": "CERTIFICATE-CODE", "licensee": "BAR Team B.V.", "date": "2025-06-04" }],
  "pr": "manual-audit", "date": "2026-11-06", "source": "manual-audit"
}
```

- `contributor` stays the person who integrated or edited the file.
- `basis` is `purchased`, `assignment`, `in-house` or (default) `contributed`;
  `rights_holder` names the owner where it is not the contributor.
- `sources` holds the purchase record; `distribution: release-only` tells the
  build which pack the file ships in.

`ledger lint` fails a release-only row or store license in a `public` repository,
a purchased row without `sources`, a row whose license `requires_rights_holder`
without one, and any row under a catalogue entry marked `draft`; it warns about
store flags that have not been turned into a license and about sources without an
item id.

Owned work (commissioned or in-house) uses a licence whose catalogue entry has
`requires_rights_holder` (and `rights_holder_default`); the auditor's Edit › Mark as
Owned Work records licence, `rights_holder` and `basis` (`assignment` or `in-house`)
while the contributor stays the credited author. `LicenseRef-BAR-Proprietary-1.0` is
in the catalogue as a draft until counsel approves its text.

### REUSE

`python tools/ledger.py reuse <repo>` regenerates `REUSE.toml`; run it after every
ledger change that goes upstream. With it, `reuse lint` on the game repository reports
exactly the files whose authors have not licensed them yet (and nothing else), so it
doubles as the campaign's progress check. Keep only licence texts that some file uses
in the public repository's `LICENSES/`; the command names the unused ones.

Workflow: mark the files in the auditor (Edit › Mark as Purchased…, Ctrl+Shift+P,
which sets license, basis, distribution and one source and drops the store flag),
let lint show the rows that must leave, then
`python tools/ledger.py move <public-repo> <asset-repo> --flag sketchfab ... --apply`.
The move copies each file to the same path in the destination, removes it from
the source (`git rm`), carries the row with `moved_from` set and review flags
kept, and regenerates manifests on both sides. Rows selected by flag get the
store license named in the catalogue; `--list` plus `--to-license` handles files
without a flag, such as the Pixabay mod-broadcast sounds.

## Retroactive consent on issues

Legacy assets carry `status: "awaiting_license"` rows that name the author but no
license. Authors answer on a GitHub issue instead of a PR:

1. A maintainer opens one issue per author with the body from
   `python tools/ledger.py consent-body <repo> --contributor "<ledger name>"`.
2. The author replies `/confirm-license cla` or `/confirm-license cc-by-sa`
   (any id from `pr_choices` also works). `/confirm-license` alone lists the
   options and how many files are waiting.
3. Job 8 (`consent-on-issue`) posts a ⏳ placeholder at once, sets that license
   on every awaiting row credited to the commenter, records the issue number as
   `pr` and `source: "consent-issue"`, regenerates the affected manifests and
   commits. Once the push has landed it replaces the placeholder with a
   per-folder count, or with a request to repeat the command if the job failed.

The issue body and the bot's replies take the command lines from the same
`licensing.json` entries (`name`, `consent_blurb` or `blurb`, `doc`), so they
read alike.

The commenter's GitHub login must match the ledger `contributor` value, or be
mapped to it in `.github/contributors.json`:

```json
{ "Mr Bob": { "github": "mrbob-gh" } }
```

Rows credited to a name nobody is mapped to stay awaiting; the bot says so when
asked. Nothing in this flow lets a user license files credited to someone else.

## Private mirror and the vertical slice

`Beyond-All-Reason-Private` mirrors public `master` and carries the private
branches. Because the ledgers, manifests,
config and library live in the tree, they arrive in the mirror with every sync
and travel back with a branch when it merges into public master. Two things
differ per repository and are handled by `overrides` in `.github/licensing.json`,
keyed by `owner/name` and applied when `GITHUB_REPOSITORY` matches:

- extra `pr_choices` and `licenses` for commissioned work that is assigned to
  BAR Team B.V. rather than licensed;
- a different `bot` identity or status-check name if wanted.

The bot must be installed on the private repository too (`CLA_BOT_TOKEN`, labels,
branch protection). Ledger JSON is a sorted array, so two branches that add rows
in the same region can conflict on merge; resolve with `git checkout --theirs`
plus `python tools/ledger.py lint`, or switch the ledger to one record per line.
