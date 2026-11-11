#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Add SPDX tags to code files from the licence each header already states.

Files with no statement get the project default. Anything ambiguous (GPL v3,
dual, restricted, copyright without a grant, GetInfo values nobody can parse)
is left alone and listed in the report for a human.

    python tools/spdx_sweep.py <repo> [--apply] [--out report.md] [--csv rows.csv]
"""
from __future__ import annotations

import argparse
import collections
import csv
import fnmatch
import io
import os
import re
import sys

DEFAULT = "GPL-2.0-or-later"
COPYRIGHT_DEFAULT = "Beyond All Reason contributors"
EXT_COMMENT = {".lua": "--", ".glsl": "//", ".frag": "//", ".vert": "//", ".geom": "//", ".js": "//",
               ".py": "#", ".sh": "#", ".ps1": "#", ".yml": "#", ".yaml": "#"}
SKIP_DIRS = {".git", "node_modules", "LICENSES", "legal", "scripts", "recoil-lua-library"}
HEAD_LINES = 40
COPYRIGHT_RE = re.compile(r"copyright|\(c\)\s*(?:\d{4}|[A-Z])|\u00a9", re.I)
GETINFO_RE = re.compile(r"^\s*licen[sc]e\s*=\s*[\"']([^\"']*)[\"']\s*,?\s*(?:--.*)?$", re.I | re.M)
PROSE_RE = re.compile(r"(?:licen[sc]ed?\s+under|released\s+under|under\s+the\s+terms\s+of)\s+([^\n]{3,80})", re.I)
SPDX_RE = re.compile(r"SPDX-License-Identifier:", re.I)
NAMED_RE = re.compile(r"\b((?:MIT|WTFPL|BSD|Apache|ISC|zlib|LGPL|GPL)[\w./,+-]*\s+licen[sc]e)\b", re.I)
MIT_BODY_RE = re.compile(r"Permission is hereby granted, free of charge", re.I)


def decide(stmt: str, from_getinfo: bool) -> tuple[str, str]:
    """Return (spdx id or '', reason). Empty id means leave the file alone."""
    s = stmt.strip()
    low = s.lower()
    if not s:
        return DEFAULT, "no statement, project default"
    if re.search(r"ba/bar only|bar only", low):
        return "", "GPL with a BA/BAR-only restriction"
    if "glsl" in low or "beherith" in low:
        return "", "mixed Lua/GLSL statement"
    if re.search(r"\bgpl\b.*\bmit\b|\bmit\b.*\bgpl\b", low):
        return "", "dual GPL/MIT statement"
    if re.search(r"\bv?3\b|gplv3|3 or later|\(or later\)", low) and "gpl" in low:
        return "", "GPL version 3"
    if "lgpl" in low or "lesser general" in low:
        if re.search(r"2\.1", low) and re.search(r"later", low):
            return "LGPL-2.1-or-later", "LGPL 2.1 or later stated"
        return "", "LGPL without a clear version"
    if re.search(r"public domain|^pd$", low):
        return "LicenseRef-Public-Domain", "public domain stated"
    if "wtfpl" in low:
        return "WTFPL", "WTFPL stated"
    if re.search(r"\bmit\b", low):
        return "MIT", "MIT stated"
    if re.search(r"\bbsd\b", low):
        return "", "BSD without a clause count"
    gpl = re.search(r"\bgpl\b|gplv|gpl-|gpl2|gnu general public|gnu license|gnu gpl", low)
    if gpl:
        if re.search(r"or later|or whatever|or late\b|\+\s*$|-or-later|2\.0\+|2\+", low):
            return DEFAULT, "GPL v2 or later stated"
        if re.search(r"\bv?2\b|gplv2|gpl2|2\.0|gpl-v2|gpl v2", low):
            return "GPL-2.0-only", "GPL v2 stated without 'or later'"
        return DEFAULT, "bare GPL, project default"
    if from_getinfo:
        return "", f"unparsed GetInfo value {s!r}"
    return DEFAULT, "prose mentions no licence, project default"


def inspect(path: str, override: tuple[str, str] | None = None) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:] if bom else raw
    try:
        head = "\n".join(text.decode("utf-8", errors="replace").splitlines()[:HEAD_LINES])
    except Exception:
        head = ""
    info = {"path": path, "bom": bom, "raw": raw}
    if SPDX_RE.search(head):
        info.update(spdx="", reason="already tagged", has_copyright=True, skip=True)
        return info
    m = GETINFO_RE.search(head)
    stmt, from_getinfo = (m.group(1), True) if m else ("", False)
    if not stmt:
        m = PROSE_RE.search(head) or NAMED_RE.search(head)
        stmt = m.group(1) if m else ""
    if not stmt and MIT_BODY_RE.search(head):
        stmt = "MIT"
    has_copyright = bool(COPYRIGHT_RE.search(head))
    spdx, reason = decide(stmt, from_getinfo)
    if override:
        spdx, reason = override[0], "set on the command line"
        info["copyright_text"] = override[1]
    if spdx == DEFAULT and not stmt and has_copyright:
        spdx, reason = "", "copyright notice without a licence grant"
    info.update(stmt=stmt, spdx=spdx, reason=reason, has_copyright=has_copyright, skip=not spdx)
    return info


def tagged(info: dict, comment: str) -> bytes:
    raw = info["raw"]
    body = raw[3:] if info["bom"] else raw
    nl = b"\r\n" if b"\r\n" in body[:2000] else b"\n"
    lines = [f"{comment} SPDX-License-Identifier: {info['spdx']}".encode()]
    if not info["has_copyright"]:
        lines.insert(0, f"{comment} SPDX-FileCopyrightText: {info.get('copyright_text') or COPYRIGHT_DEFAULT}".encode())
    header = nl.join(lines) + nl
    if body.startswith(b"#!"):
        first, _, rest = body.partition(nl)
        body = first + nl + header + rest
    else:
        body = header + body
    return (b"\xef\xbb\xbf" if info["bom"] else b"") + body


def walk(repo: str):
    for dp, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")] if dp == repo else \
                  [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in EXT_COMMENT:
                yield os.path.join(dp, f), EXT_COMMENT[ext]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--apply", action="store_true", help="write the tags (default: report only)")
    ap.add_argument("--out", default="", help="markdown report")
    ap.add_argument("--csv", default="", help="per-file decisions")
    ap.add_argument("--set", action="append", default=[], metavar="GLOB=ID[:COPYRIGHT]",
                    help="decide these files by hand, e.g. 'modules/i18n/**=MIT:2014 Enrique Garcia Cota'")
    a = ap.parse_args(argv)
    repo = os.path.abspath(a.repo)
    overrides = []
    for item in a.set:
        glob, _, val = item.partition("=")
        spdx, _, cr = val.partition(":")
        overrides.append((glob, spdx, cr))
    rows = []
    for path, comment in walk(repo):
        rel = os.path.relpath(path, repo).replace("\\", "/")
        ov = next(((sp, cr) for g, sp, cr in overrides if fnmatch.fnmatch(rel, g)), None)
        info = inspect(path, ov)
        rows.append({"file": rel, "spdx": info["spdx"], "reason": info["reason"], "statement": info.get("stmt", ""),
                     "copyright_added": "no" if info["has_copyright"] or info["skip"] else "yes"})
        if a.apply and not info["skip"]:
            with open(path, "wb") as fh:
                fh.write(tagged(info, comment))
    by_spdx = collections.Counter(r["spdx"] or "(left alone)" for r in rows)
    by_reason = collections.Counter(r["reason"] for r in rows)
    print(f"{len(rows)} code files; {'tagged' if a.apply else 'would tag'} {sum(1 for r in rows if r['spdx'])}")
    for k, n in by_spdx.most_common():
        print(f"  {n:5d}  {k}")
    print("left alone:")
    for k, n in by_reason.most_common():
        if k and not any(r["spdx"] for r in rows if r["reason"] == k):
            print(f"  {n:5d}  {k}")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: r["file"]))
    if a.out:
        lines = ["# SPDX sweep report", "", f"Repository: `{repo}`. {len(rows)} code files outside `scripts/`.", "",
                 "## Tags", "", "| SPDX id | Files |", "|---|---|"]
        lines += [f"| `{k}` | {n} |" for k, n in by_spdx.most_common()]
        lines += ["", "## Reasons", "", "| Reason | Files |", "|---|---|"]
        lines += [f"| {k} | {n} |" for k, n in by_reason.most_common()]
        for reason in sorted({r["reason"] for r in rows if not r["spdx"] and r["reason"] != "already tagged"}):
            lines += ["", f"## Left alone: {reason}", "", "| File | Statement |", "|---|---|"]
            lines += [f"| `{r['file']}` | {r['statement'].replace('|', '/')} |" for r in sorted(rows, key=lambda r: r["file"]) if r["reason"] == reason]
        for spdx in [k for k in by_spdx if k not in ("(left alone)", DEFAULT)]:
            lines += ["", f"## Tagged `{spdx}`", "", "| File | Statement |", "|---|---|"]
            lines += [f"| `{r['file']}` | {r['statement'].replace('|', '/')} |" for r in sorted(rows, key=lambda r: r["file"]) if r["spdx"] == spdx]
        bare = [r for r in rows if r["reason"] == "bare GPL, project default"]
        if bare:
            lines += ["", "## Tagged with the default on a bare GPL mention", "", "| File | Statement |", "|---|---|"]
            lines += [f"| `{r['file']}` | {r['statement'].replace('|', '/')} |" for r in sorted(bare, key=lambda r: r["file"])]
        io.open(a.out, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
