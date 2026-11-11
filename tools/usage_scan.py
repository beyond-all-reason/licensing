# SPDX-License-Identifier: GPL-2.0-or-later
"""Find art files that nothing in the repository references, and say where the rest are used.

    python tools/usage_scan.py <repo> [--flag unused] [--all] [--out report.md] [--list unreferenced.txt]

A file counts as referenced when its basename or stem appears in a text
source or inside an .s3o model (texture names), when code lists its directory
(the directory path appears as a quoted string), or when its stem is a unit
name or wreck of one. The report names up to three referencing files so a
stale flag can be cleared, or a dead reference spotted, without a second search.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from license_auditor import data as D  # noqa: E402

TEXT_EXT = {".lua", ".txt", ".tdf", ".json", ".yml", ".yaml", ".cfg", ".ini", ".glsl", ".frag", ".vert",
            ".bos", ".h", ".py", ".toml", ".csv", ".xml", ".rml", ".rcss", ".html", ".js", ".css"}
WRECK = ("_dead2", "_dead", "_heap")
CODE_TOPS = ("luaui", "luarules", "luaintro", "gamedata", "effects", "common", "modules", "features", "scripts",
             "shaders", "modelmaterials_gl4", "singleplayer", "mapgenerator")
MAX_SITES = 3


def _build_index(repo: Path):
    """Lowercased text per code/config file (s3o texture names count as text), listed directories, unit names."""
    chunks: dict[str, str] = {}
    bookkeeping = {".github/file-license-assignments.json", ".github/cla-signatures.json",
                   D.COMMENTS_FLAGS_FILENAME, "REUSE.toml", "LICENSE.md", "CONTRIBUTING.md"}
    for dirpath, dnames, fnames in os.walk(repo):
        dnames[:] = [d for d in dnames if d not in (".git", "legal", "LICENSES")]
        rel_dir = os.path.relpath(dirpath, repo).replace(os.sep, "/")
        for fn in fnames:
            ext = os.path.splitext(fn)[1].lower()
            p = Path(dirpath) / fn
            rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            if rel in bookkeeping or fn == "LICENSE_MANIFEST.md":
                continue
            if ext in TEXT_EXT:
                try:
                    chunks[rel] = p.read_text(encoding="utf-8", errors="replace").lower()
                except OSError:
                    pass
            elif ext == ".s3o":
                try:
                    raw = p.read_bytes()
                except OSError:
                    continue
                names = re.findall(rb"[\w\-./]+\.(?:dds|png|tga|bmp|jpg|jpeg)", raw, re.I)
                if names:
                    chunks[rel] = "\n".join(n.decode("ascii", "replace").lower() for n in names)
    text = "\n".join(chunks.values())
    dirs_in_code: set[str] = set()
    for m in re.finditer(r"[\"']((?:[a-z0-9_\-]+/)+)[\"']", text):
        dirs_in_code.add(m.group(1).rstrip("/"))
    units: set[str] = set()
    for dirpath, _, fnames in os.walk(repo / "units"):
        for fn in fnames:
            if fn.lower().endswith(".lua"):
                units.add(fn[:-4].lower())
    return chunks, text, dirs_in_code, units


def _unit_stem(stem: str) -> str:
    s = re.sub(r"(?:_\d+_\d+)?_aoplane(?:_scav)?$", "", stem)
    for w in WRECK:
        if s.endswith(w):
            return s[: -len(w)]
    return s


def _kind(site: str) -> str:
    if site.startswith("units/"):
        return "unitdef"
    if site.endswith(".s3o"):
        return "model"
    if site.split("/", 1)[0] in CODE_TOPS or site.endswith((".lua", ".glsl", ".frag", ".vert", ".bos")):
        return "code"
    return "config"


def references(rel: str, chunks: dict[str, str], text: str, dirs_in_code: set[str], units: set[str],
               flagged: set[str]) -> tuple[list[str], list[str]]:
    """Return (hit kinds, referencing files). Sites only from other flagged files are marked."""
    base = os.path.basename(rel).lower()
    stem = os.path.splitext(base)[0]
    hits: list[str] = []
    sites: list[str] = []
    stem_re = re.compile(r"(?<![a-z0-9_])" + re.escape(stem) + r"(?![a-z0-9_])")
    if base in text:
        hits.append("basename")
        sites = [p for p, t in chunks.items() if base in t and p != rel]
    elif stem_re.search(text):
        hits.append("stem")
        sites = [p for p, t in chunks.items() if stem in t and stem_re.search(t) and p != rel]
    d = rel.rsplit("/", 1)[0].lower()
    if d in dirs_in_code:
        hits.append(f"dir listed: {d}")
    if _unit_stem(stem) in units:
        hits.append("unit " + _unit_stem(stem))
    if rel.startswith("anims/") and stem.startswith("cursor"):
        hits.append("cursor by name")
    if sites and all(s in flagged for s in sites):
        hits.append("only from other flagged files")
    if hits[:1] == ["stem"] and len(stem) <= 3:
        hits[0] = "short stem"
    return hits, sites


def verdict(hits: list[str]) -> str:
    if not hits:
        return "none"
    if "only from other flagged files" in hits:
        return "flagged-only"
    if all(h.startswith(("dir listed", "short stem")) for h in hits):
        return "weak"
    return "referenced"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--flag", default="unused", help="only files carrying this flag (default unused)")
    ap.add_argument("--all", action="store_true", help="scan every ledger file instead of flagged ones")
    ap.add_argument("--out", default="", help="markdown report path")
    ap.add_argument("--list", default="", help="write unreferenced paths here, one per line")
    ns = ap.parse_args(argv)
    repo = Path(ns.repo).resolve()
    D.apply_config(D.load_config(repo))
    assigns = json.loads((repo / ".github" / "file-license-assignments.json").read_text(encoding="utf-8-sig"))
    flags_path = repo / D.COMMENTS_FLAGS_FILENAME
    flags = json.loads(flags_path.read_text(encoding="utf-8-sig")) if flags_path.exists() else {"files": []}
    flagged = {v["file"] for v in flags.get("files", []) if ns.flag in v.get("flags", [])}
    files = [e["file"] for e in assigns] if ns.all else sorted(flagged)
    chunks, text, dirs_in_code, units = _build_index(repo)
    rows = []
    for f in files:
        if not (repo / f).exists():
            continue
        hits, sites = references(f, chunks, text, dirs_in_code, units, flagged)
        rows.append((f, hits, sites, verdict(hits)))
    counts = {v: sum(1 for r in rows if r[3] == v) for v in ("none", "flagged-only", "weak", "referenced")}
    print(f"scanned {len(rows)} file(s): {counts['none']} unreferenced, {counts['flagged-only']} referenced only by "
          f"other flagged files, {counts['weak']} only via a listed directory, {counts['referenced']} referenced by name")
    unref = [f for f, _, _, v in rows if v == "none"]
    if ns.out:
        lines = [f"# Usage scan ({'all ledger files' if ns.all else 'flag ' + ns.flag})", "",
                 f"{len(rows)} files scanned: {counts['none']} without any reference, {counts['flagged-only']} referenced "
                 f"only by other flagged files (dead together), {counts['weak']} reachable only because code lists their "
                 f"directory (review by hand), {counts['referenced']} referenced by name.", ""]
        for v, title in (("none", "No reference"), ("flagged-only", "Referenced only by other flagged files"),
                         ("weak", "Only via a listed directory"), ("referenced", "Referenced by name: clear the flag or follow the reference")):
            sel = [r for r in rows if r[3] == v]
            if not sel:
                continue
            lines += [f"## {title} ({len(sel)})", "", "| File | References | Where |", "|---|---|---|"]
            for f, hits, sites, _ in sel:
                where = ", ".join(f"`{s}` ({_kind(s)})" for s in sites[:MAX_SITES])
                if len(sites) > MAX_SITES:
                    where += f" +{len(sites) - MAX_SITES}"
                lines.append(f"| `{f}` | {', '.join(hits) if hits else '**none**'} | {where} |")
            lines.append("")
        Path(ns.out).write_text("\n".join(lines), encoding="utf-8", newline="\n")
    if ns.list:
        Path(ns.list).write_text("\n".join(unref) + ("\n" if unref else ""), encoding="utf-8", newline="\n")
    if not ns.out:
        for f, hits, sites, v in rows:
            if v != "none":
                print(f"  {v:12s} {f}  ({', '.join(hits)}; {', '.join(sites[:MAX_SITES])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
