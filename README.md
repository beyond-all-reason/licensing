# BAR licensing

Shared licensing machinery for the beyond-all-reason organisation: the CLA
bot as a reusable GitHub Actions workflow, the rules it reads, the legal
texts contributors agree to, and the tools that keep the per-file ledgers
honest.

## What is here

| Path | Purpose |
|---|---|
| `.github/workflows/cla-check.yml` | The bot, as a reusable workflow (`on: workflow_call`) |
| `.github/workflows/cla-check-stub.yml` | The stub a repository copies to adopt the bot |
| `.github/scripts/licensing.js` | Bot logic, shared with the desktop auditor; `licensing.test.js` runs with `node` |
| `.github/licensing.json` | Rules: art directories, extensions, licence catalogue, PR choices, ledger paths, bot identity |
| `legal/` | Project licence policy, CLA, CC-BY-SA opt-in, FAQ |
| `LICENSES/` | Full texts for every identifier in the catalogue (REUSE layout) |
| `tools/ledger.py` | Ledger CLI: `stats`, `lint`, `rebase`, `manifests`, `reuse`, `consent-body`, `apply-consent`, `move` |
| `tools/license_auditor/` | PyQt desktop auditor for bulk bookkeeping |
| `tools/check_workflow.py` | Syntax and policy checks for the workflow files |
| `IMPLEMENTATION_GUIDE.md` | How the bot, ledgers, manifests and tools fit together |

The ledgers and art fixtures in this repository exist so a pull request here
exercises the bot end to end; the stub calls the local workflow file.

## Adopting the bot in a repository

1. Copy `cla-check-stub.yml` to `.github/workflows/cla-check.yml` and set
   `uses:` to `beyond-all-reason/licensing/.github/workflows/cla-check.yml@main`
   (a tag or commit SHA pins a version; the scripts follow the pinned commit).
2. Add `.github/licensing.json` (start from the one here), an empty
   `.github/cla-signatures.json` (`[]`) and `.github/file-license-assignments.json`
   (`[]`), and `.github/contributors.json` mapping display names to logins.
3. Create the labels the catalogue names plus `needs-licensing-review` and
   `asset: replaced`; enable issues (retroactive consent runs on issues).
4. Give the bot an identity: a `CLA_BOT_TOKEN` secret (GitHub App installation
   token or a PAT with `contents: write`). Without it the workflow token is
   used, which cannot push to a protected branch.
5. Point `CODEOWNERS` at the licensing paths.

Contributors see one thing: a PR that adds art gets a licence choice in its
description and a `/confirm-license` command; everything else happens on
merge. See `IMPLEMENTATION_GUIDE.md` for the flows and the ledger format.

## Development

```sh
node .github/scripts/licensing.test.js
python tools/test_ledger.py
python tools/check_workflow.py .github/workflows/cla-check.yml
python tools/check_workflow.py .github/workflows/cla-check-stub.yml
```

The auditor needs `pip install -r tools/license_auditor/requirements.txt` and
runs with `python -m tools.license_auditor <path-to-repo>`.

## Licence

Workflow and tool code: GPL-2.0-or-later. Legal texts in `legal/` and
`LICENSES/`: see each file.
