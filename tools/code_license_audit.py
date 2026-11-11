# SPDX-License-Identifier: GPL-2.0-or-later
"""Report the license statements found in code files.

    python tools/code_license_audit.py <repo> [--out report.md] [--csv rows.csv]

Looks at the first 60 lines of every code file (and widget/gadget GetInfo
tables) for SPDX tags, `license = "..."` fields and licence words, then
classifies each file: spdx, gpl, gpl-compatible, public-domain, incompatible,
unknown-statement, none.
"""
from __future__ import annotations

import argparse
import collections
import csv
import os
import re
import sys
from pathlib import Path

CODE_EXT = {".lua", ".glsl", ".frag", ".vert", ".glslf", ".glslv", ".bos", ".h", ".py", ".js", ".ts", ".sh", ".ahk"}
SKIP_DIRS = {".git", "node_modules", "LICENSES"}
COMPATIBLE = re.compile(r"\b(mit licen[sc]e|x11|bsd|zlib|apache|isc licen[sc]e|lgpl|lesser general public|mpl|mozilla public|"
                        r"boost software|wtfpl|unlicense|cc0|creative commons zero)\b", re.I)
INCOMPATIBLE = re.compile(r"(nc-nd|non-?commercial|cc[ -]by[ -]nc|all rights reserved|proprietary)", re.I)
GPL = re.compile(r"\b(a?gplv?[23]?|gnu general public)\b", re.I)


def classify(head: str) -> tuple[str, str]:
    low = head.lower()
    m = re.search(r"spdx-license-identifier:\s*([^\s*/]+)", head, re.I)
    if m:
        return "spdx", m.group(1)
    # A GetInfo field: `license = "..."` on its own line, nothing else before it.
    m = re.search(r"^\s*licen[sc]e\s*=\s*[\"']([^\"']*)[\"']\s*,?\s*(?:--.*)?$", head, re.I | re.M)
    stmt = m.group(1).strip() if m else ""
    if not stmt:
        m = re.search(r"(?:licen[sc]ed?\s+under|under\s+the)\s+([^\n.]{3,60})", head, re.I)
        stmt = m.group(1).strip() if m else ""
    probe = stmt or low
    if INCOMPATIBLE.search(probe):
        return "incompatible", stmt or "statement in header"
    if re.search(r"\bpublic domain\b|^pd$", probe, re.I):
        return "public-domain", stmt or "public domain"
    if COMPATIBLE.search(probe) and not re.search(r"\bgpl\b", probe, re.I):
        return "gpl-compatible", stmt or "permissive mentioned in header"
    if GPL.search(probe):
        return "gpl", stmt or "GPL mentioned in header"
    if COMPATIBLE.search(probe):
        return "gpl-compatible", stmt or "permissive mentioned in header"
    if stmt:
        return "unknown-statement", stmt
    if "copyright" in low or "(c)" in low:
        return "copyright-only", ""
    return "none", ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--out", default="")
    ap.add_argument("--csv", default="")
    ns = ap.parse_args(argv)
    repo = Path(ns.repo).resolve()
    rows = []
    for dirpath, dnames, fnames in os.walk(repo):
        dnames[:] = [d for d in dnames if d not in SKIP_DIRS]
        for fn in fnames:
            if os.path.splitext(fn)[1].lower() not in CODE_EXT:
                continue
            p = Path(dirpath) / fn
            try:
                with p.open(encoding="utf-8", errors="replace") as fh:
                    head = "".join(fh.readline() for _ in range(60))
            except OSError:
                continue
            kind, stmt = classify(head)
            rel = p.relative_to(repo).as_posix()
            rows.append((rel.split("/")[0], rel, kind, stmt))
    by_kind = collections.Counter(k for _, _, k, _ in rows)
    by_dir = collections.defaultdict(collections.Counter)
    for top, _, k, _ in rows:
        by_dir[top][k] += 1
    print(f"{len(rows)} code files")
    for k, n in by_kind.most_common():
        print(f"  {n:6d}  {k}")
    lines = ["# Code license audit", "", f"{len(rows)} code files scanned.", "", "## By classification", "",
             "| Class | Files |", "|---|---|"] + [f"| {k} | {n} |" for k, n in by_kind.most_common()]
    lines += ["", "## By top-level directory", "", "| Dir | " + " | ".join(by_kind) + " |", "|---|" + "---|" * len(by_kind)]
    for top in sorted(by_dir):
        lines.append(f"| {top} | " + " | ".join(str(by_dir[top][k]) for k in by_kind) + " |")
    v3 = [(rel, stmt) for _, rel, k, stmt in rows if k == "gpl" and re.search(r"v?3", stmt or "")]
    if v3:
        lines += ["", f"## GPL version 3 statements ({len(v3)}): compatible with v2-or-later code, but the combination becomes v3",
                  "", "| File | Statement |", "|---|---|"] + [f"| `{r}` | {s} |" for r, s in sorted(v3)]
    for kind in ("incompatible", "unknown-statement", "copyright-only", "gpl-compatible", "public-domain"):
        sel = [(rel, stmt) for _, rel, k, stmt in rows if k == kind]
        if not sel:
            continue
        lines += ["", f"## {kind} ({len(sel)})", "", "| File | Statement |", "|---|---|"]
        lines += [f"| `{rel}` | {stmt.replace('|', '/')} |" for rel, stmt in sorted(sel)]
    stmts = collections.Counter(stmt for _, _, k, stmt in rows if k in ("gpl", "unknown-statement") and stmt)
    lines += ["", "## Distinct license strings (gpl + unknown)", "", "| Statement | Files |", "|---|---|"]
    lines += [f"| {s.replace('|', '/')} | {n} |" for s, n in stmts.most_common(60)]
    if ns.out:
        Path(ns.out).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    if ns.csv:
        with open(ns.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["top", "file", "class", "statement"])
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
