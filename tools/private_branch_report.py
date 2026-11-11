#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Art on a branch that no ledger knows, with an author hint per file.

    python tools/private_branch_report.py <clone> <branch> --known <repo>... --out <base>

Works on a blobless clone: the file list comes from the tree, the hints from
git history and the branch's unitdef author fields. Writes <base>.md and
<base>.csv.
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from license_auditor import data as D  # noqa: E402


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def unitdef_authors(repo: str, branch: str) -> dict[str, str]:
    """Extract units/ from the branch into a temp dir and reuse the auditor's parser."""
    with tempfile.TemporaryDirectory() as tmp:
        tar = subprocess.run(["git", "-C", repo, "archive", "--format=tar", branch, "units"],
                             capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
            members = [m for m in tf.getmembers() if m.isfile() and not m.name.startswith(("/", ".."))]
            tf.extractall(tmp, members=members)
        return D._load_unitdef_authors(Path(tmp))


def first_commits(repo: str, branch: str) -> dict[str, tuple[str, str, str]]:
    log = git(repo, "log", branch, "--name-status", "--diff-filter=A", "--format=@@%an|%ad|%s",
              "--date=short", "--", *D.ART_TOP_DIRS)
    first: dict[str, tuple[str, str, str]] = {}
    cur = None
    for line in log.splitlines():
        if line.startswith("@@"):
            cur = tuple(line[2:].split("|", 2))
        elif line.startswith("A\t") and cur:
            first.setdefault(line[2:], cur)
    return first


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clone")
    ap.add_argument("branch")
    ap.add_argument("--known", action="append", default=[], help="repository whose ledger and art tree count as known")
    ap.add_argument("--out", required=True, help="base path for the .md and .csv")
    a = ap.parse_args(argv)
    D.apply_config(D.load_config(Path(a.known[0]))) if a.known else None
    known: set[str] = set()
    for repo in a.known:
        ledger = Path(repo) / ".github" / "file-license-assignments.json"
        if ledger.exists():
            import json
            known |= {r["file"] for r in json.loads(ledger.read_text(encoding="utf-8-sig"))}
        for top in D.ART_TOP_DIRS:
            for dp, _, fs in os.walk(Path(repo) / top):
                for f in fs:
                    known.add(os.path.relpath(os.path.join(dp, f), repo).replace("\\", "/"))
    tree = git(a.clone, "ls-tree", "-r", "--name-only", a.branch).splitlines()
    files = [p for p in tree if D.art_top_for_path(p) and not D.is_non_asset(os.path.basename(p)) and p not in known]
    units = unitdef_authors(a.clone, a.branch)
    first = first_commits(a.clone, a.branch)
    rows = []
    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0].lower()
        ua = next((units[k] for k in D.unit_key_candidates(stem) if k in units), "")
        an, ad, subj = first.get(f, ("", "", ""))
        pr = re.search(r"#(\d+)", subj or "")
        rows.append({"file": f, "dir": D.art_top_for_path(f), "unitdef_author": ua, "git_author": an, "added": ad,
                     "pr": pr.group(1) if pr else "", "suggested": ua or an})
    with open(a.out + ".csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "dir", "unitdef_author", "git_author", "added", "pr", "suggested"])
        w.writeheader()
        w.writerows(rows)
    by_dir = collections.Counter(r["dir"] for r in rows)
    by_sug = collections.Counter(r["suggested"] or "(none)" for r in rows)
    sub = collections.Counter(r["file"].rsplit("/", 1)[0] for r in rows)
    short = a.branch.split("/")[-1]
    lines = [f"# `{short}`: art without a ledger row", "",
             f"{len(rows)} files on `{a.branch}` that no known ledger or art tree has. Suggested author = unitdef "
             "author field, else the git author of the first commit (often the integrator).", "",
             "## By directory", "", "| Dir | Files |", "|---|---|"] + [f"| {d} | {n} |" for d, n in by_dir.most_common()]
    lines += ["", "## By suggested author", "", "| Suggested | Files |", "|---|---|"] + [f"| {s} | {n} |" for s, n in by_sug.most_common()]
    lines += ["", "## By folder", "", "| Folder | Files |", "|---|---|"] + [f"| `{d}` | {n} |" for d, n in sub.most_common(40)]
    io.open(a.out + ".md", "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print(f"{short}: {len(rows)} files; by dir {by_dir.most_common(6)}; by author {by_sug.most_common(8)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
