# BAR License Auditor

A PyQt6 desktop tool for tracking and assigning license metadata to art/audio/code files in the BAR repository.

## Requirements

- Python 3.10+
- PyQt6 ≥ 6.4.0, Pillow ≥ 10.0.0

## Launch

**Windows (recommended)**
```
tools\run_auditor.bat [path\to\bar\repo]
```
Installs dependencies automatically if missing. If no path is given, a folder-picker opens on startup.

**CLI**
```
cd tools
python -m license_auditor [/path/to/bar/repo]
```

## UI Overview

The main window shows a filterable table of all tracked art files.

| Column | Description |
|---|---|
| Status | `Gap` (no data) · `Intent` · `Confirmed` · `Merged` |
| Path | Repo-relative file path |
| License | Assigned SPDX identifier |
| Contributor | GitHub handle of the rights holder |
| Actions | Quick-action buttons per row |

### Actions buttons (per row)

| Button | Effect |
|---|---|
| **Author** | Mark contributor as confirmed — a row without a license becomes *Awaiting license* (persisted on save) |
| **CLA** | Assign `LicenseRef-BAR-CLA-1.0` |
| **CC** | Assign `CC-BY-SA-4.0` |
| **GPL** | Assign `GPL-2.0-or-later` |
| **MIT** | Assign `MIT` |
| **CC0** | Assign `CC0-1.0` (public domain) |

The License combo (inline edit, bulk dialog, quick-assign menu) also offers `OFL-1.1`, `CC-BY-SA-3.0` and `CC-BY-NC-ND-4.0` for fonts, imported CC assets and legacy rows. Full texts for every offered license live in `LICENSES/`.
| **✕** | Clear license + contributor |

Assigning `CC0-1.0` (public domain) fills **Contributor** with `public domain` instead of the prefilled author — there is no rights holder to record. Switching such a row to another license restores the normal prefill (unitdef author → PR submitter → git author).

Double-click the **License** or **Contributor** cell to edit inline.

### Keyboard shortcuts

| Key | Action |
|---|---|
| `1` | Confirm author (selected rows) |
| `2` | Assign CLA |
| `3` | Assign CC-BY-SA-4.0 |
| `4` | Assign GPL-2.0-or-later |
| `N` / `Shift+N` | Next / previous gap row |
| `Space` | Toggle preview strip |
| `Ctrl+S` | Save changes |
| `Ctrl+Shift+E` | Export visible rows to CSV |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
| `F1` / `?` | Show keyboard shortcut overlay |

### Toolbar / menus

- **Open Repo** — load a different BAR repo directory
- **⟳ Refresh** — re-scan without restarting
- **Edit › Edit Selected…** — bulk-edit license/contributor/flag for multiple rows
- **Bottom bar > Flag** — add the flag chosen in the adjacent combo to all selected rows (`(none)` clears all flags). A file can carry multiple flags; they are stored in `license_comments_flags.json`, separately from the license ledger.
- **Edit > Flag Wizard…** — create or remove custom flags (e.g. one per asset store: Envato, Sketchfab, TurboSquid) alongside the built-in ones. Custom flag definitions are shared with the team via `license_comments_flags.json`.
- **Edit › Mark as Purchased… (Ctrl+Shift+P)** — record the selected files as store purchases: store license, `basis: purchased`, `distribution: release-only`, one `sources` entry; the matching store flag is dropped. Such rows must then leave a public repository with `python tools/ledger.py move`.
- **Edit › Mark as Owned Work…** — record commissioned or in-house files: a licence that names a rights holder (default BAR Team B.V.) plus `basis` assignment or in-house; the contributor stays the credited author.
- Both actions first offer the unlicensed files of the same units (wrecks, unitpics, decals), so marking a main model covers its artefacts in one go.
- **Edit › Import CSV…** — import assignments from a CSV export
- **Edit › Commit & Push…** — commit the `assignments.json` and push to origin
- **View › Columns…** — toggle visible columns
- **View › Compact Mode** — reduce row height
- **View › Dark / Light** — switch theme

## How data is retrieved

On startup (or after **⟳ Refresh**) the auditor does two passes:

1. **Disk scan** — walks the art top-level directories (`anims/`, `bitmaps/`, `fonts/`, `icons/`, `modelmaterials/`, `modelmaterials_gl4/`, `music/`, `objects3d/`, `sidepics/`, `sounds/`, `unitpics/`, `unittextures/`) and creates one row per file found. Code (shaders, BOS/COB, Lua, Python), docs (`.md`, `.ipynb`), `LICENSE_MANIFEST.md` and license notice files (`license.txt`, `OFL*.txt`) are skipped: they are never assets.

2. **Cross-reference with ledger files** — each file path is looked up in the two JSON ledgers that live in `.github/` of the BAR repo:
   - `.github/cla-signatures.json` — contributor intent records (status `intent`)
   - `.github/file-license-assignments.json` — confirmed/audited assignments (status `confirmed`)
   
   Files absent from both ledgers get status `gap`.

3. **Git history enrichment** — a single `git log --name-status` pass over the art directories populates:
   - **Last changed** date (most recent commit touching the file)
   - **Git author** (GitHub handle inferred from the oldest commit's noreply email)
   - **PR number / URL** (extracted from merge commit subjects like `Merge pull request #NNN`)
   - **Merged** status upgrade — `confirmed` rows whose PR appears as a merged commit are promoted to `merged`

4. **Unitdef author correlation** — for `objects3d/`, `unitpics/`, and `unittextures/`, the auditor scans `units/*.lua` for `model_author` / `author` fields and pre-fills the Contributor cell for gap rows where a matching model name is found. Wreck / heap variants (`objects3d/armsilo_dead.s3o`) and ground decals (`unittextures/decals_features/armsilo_dead_8_8_aoplane.dds`) resolve to their parent unit (`armsilo.lua`) the same way unitpics do.

## Data persistence

`Ctrl+S` (or **Edit › Save Changes**) writes dirty rows back to `.github/file-license-assignments.json` inside the loaded repo. Gap rows with cleared data are removed from the ledger; confirmed rows are upserted with a timestamp and `"source": "manual-audit"`. Keys the auditor does not edit (`rights_holder`, `basis`, `sources`, `distribution`, `moved_from`) are preserved.

After the ledger is written, a per-top-dir `LICENSE_MANIFEST.md` is regenerated inside every art directory that has at least one recorded asset (`anims/`, `bitmaps/`, `fonts/`, `icons/`, `modelmaterials/`, `modelmaterials_gl4/`, `music/`, `objects3d/`, `sidepics/`, `sounds/`, `unitpics/`, `unittextures/`). This mirrors the manifest format produced by the `confirm-on-merge` workflow so working copies stay in sync without waiting for a PR merge.

Changes are local to disk until you commit.

## Committing and pushing

**Edit › Commit & Push…** opens a dialog that:

1. Shows a summary and per-record diff of what will be written.
2. Saves the JSON ledger (same as `Ctrl+S`).
3. Runs the following git commands inside the loaded repo:
    ```
    git add .github/cla-signatures.json .github/file-license-assignments.json <art-dir>/LICENSE_MANIFEST.md ...
    GIT_AUTHOR_NAME="BAR CLA Bot" GIT_COMMITTER_NAME="BAR CLA Bot" \
    GIT_AUTHOR_EMAIL="cla-bot@beyondallreason.info" \
    GIT_COMMITTER_EMAIL="cla-bot@beyondallreason.info" \
    git commit -m "<your message>"
    git push          # only if "Push to remote" is checked
    ```
   The bot identity is passed via environment variables so it only applies to this commit and does not modify your local git config.
4. Streams live command output into the dialog. Any step failure stops the sequence and re-enables the buttons.

The push uses whatever remote/branch `git push` resolves to in the repo (normally `origin/main`). Your local git credentials / SSH key must already be configured.

## Logs

Errors and startup output are appended to `tools/license_auditor/auditor.log`.
