# SPDX-License-Identifier: GPL-2.0-or-later
"""Ledger maintenance for a BAR-style repo.

Usage (from the tools/ directory or with it on PYTHONPATH):
    python ledger.py stats <repo>
    python ledger.py lint <repo>
    python ledger.py rebase <repo> --old <ref> --new <ref> [--apply] [--drop-missing]
    python ledger.py manifests <repo>
    python ledger.py consent-body <repo> --contributor "Mr Bob" [--contributor MrBob] [--github login] [--out file]
    python ledger.py apply-consent <repo> --contributor NAME --license ID [--ref REF] [--source SRC] [--apply]
    python ledger.py move <src-repo> <dst-repo> [--flag F]... [--license L]... [--list FILE]
                     [--to-license L] [--keep-files] [--apply]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from license_auditor import data as D  # noqa: E402


def _read_json(p: Path, fallback):
    try:
        txt = p.read_text(encoding="utf-8-sig")
        return json.loads(txt) if txt.strip() else fallback
    except Exception:
        return fallback


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _paths(repo: Path):
    return (
        repo / ".github" / "file-license-assignments.json",
        repo / ".github" / "cla-signatures.json",
        repo / D.COMMENTS_FLAGS_FILENAME,
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0 and check:
        raise SystemExit(f"git {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout if r.returncode == 0 else ""


def _repo_slug(repo: Path) -> str:
    """owner/name from the origin remote, else the directory name."""
    url = _git(repo, "remote", "get-url", "origin", check=False).strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else repo.name


def _scan_disk(repo: Path) -> list[str]:
    out: list[str] = []
    for top in D.ART_TOP_DIRS:
        base = repo / top
        if not base.exists():
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs.sort()
            for fn in sorted(files):
                if D.is_non_asset(fn):
                    continue
                out.append(os.path.relpath(os.path.join(dirpath, fn), repo).replace(os.sep, "/"))
    return out


def _flags_by_file(flags) -> dict[str, dict]:
    if not isinstance(flags, dict):
        return {}
    return {v.get("file", ""): v for v in flags.get("files", []) if isinstance(v, dict)}


def cmd_stats(repo: Path) -> int:
    ap, _, fp = _paths(repo)
    assigns = _read_json(ap, [])
    led = {e["file"]: e for e in assigns}
    files = _scan_disk(repo)
    tot, lic, aw = collections.Counter(), collections.Counter(), collections.Counter()
    for f in files:
        top = D.art_top_for_path(f) or "?"
        tot[top] += 1
        e = led.get(f)
        if e and e.get("license"):
            lic[top] += 1
        elif e:
            aw[top] += 1
    print(f"{'dir':24s} {'files':>6s} {'licensed':>8s} {'awaiting':>8s} {'gap':>6s} {'lic%':>6s}")
    T = L = A = 0
    for top in D.ART_TOP_DIRS:
        t, l, a = tot[top], lic[top], aw[top]
        if not t:
            continue
        T += t; L += l; A += a
        print(f"{top:24s} {t:6d} {l:8d} {a:8d} {t - l - a:6d} {100 * l / t:5.1f}%")
    print(f"{'TOTAL':24s} {T:6d} {L:8d} {A:8d} {T - L - A:6d} {100 * L / T if T else 0:5.1f}%")
    print()
    print("by license:", collections.Counter(e.get("license") or "(awaiting)" for e in assigns).most_common())
    print("awaiting by contributor:",
          collections.Counter(e["contributor"] for e in assigns if not e.get("license")).most_common(15))
    flags = _read_json(fp, {}) or {}
    fc = collections.Counter(f for v in flags.get("files", []) for f in v.get("flags", []))
    print("flags:", fc.most_common())
    return 0


def _login_to_name(repo: Path) -> dict[str, str]:
    """Lowercased login -> display name, for names that differ from the login."""
    cmap = _read_json(repo / ".github" / "contributors.json", {}) or {}
    out: dict[str, str] = {}
    for name, info in cmap.items():
        if name.startswith("_"):
            continue
        gh = info.get("github", "") if isinstance(info, dict) else str(info)
        if gh and gh.lower() != name.lower():
            out[gh.lower()] = name
    return out


def cmd_lint(repo: Path) -> int:
    ap, sp, fp = _paths(repo)
    assigns = _read_json(ap, None)
    errors: list[str] = []
    warns: list[str] = []
    if not isinstance(assigns, list):
        print("ERROR: assignments ledger missing or not a JSON array")
        return 1
    sigs = _read_json(sp, None)
    if sp.exists() and not isinstance(sigs, list):
        errors.append("signatures file is not a JSON array (empty file counts: write [])")
    on_disk = set(_scan_disk(repo))
    flags = _read_json(fp, None)
    fbf = _flags_by_file(flags)
    store_flags = D.store_flags()
    public = D.REPO_DISTRIBUTION != "release-only"
    seen: set[str] = set()
    names: dict[str, set[str]] = collections.defaultdict(set)
    login_to_name = _login_to_name(repo)
    by_login: collections.Counter[str] = collections.Counter()
    flagged_unmoved = 0
    for e in assigns:
        f = e.get("file", "")
        if f in seen:
            errors.append(f"duplicate path: {f}")
        seen.add(f)
        if f not in on_disk:
            if (repo / f).exists():
                errors.append(f"non-asset recorded: {f}")
            else:
                errors.append(f"missing on disk: {f}")
        lic = e.get("license") or ""
        contrib = (e.get("contributor") or "").strip()
        if lic and lic not in D.KNOWN_LICENSES:
            errors.append(f"unknown license {lic!r}: {f}")
        if lic and not contrib:
            errors.append(f"licensed row without contributor: {f}")
        if not lic and e.get("status") != "awaiting_license":
            errors.append(f"row without license and without awaiting status: {f}")
        if not lic and not contrib:
            errors.append(f"awaiting row without contributor: {f}")
        if contrib:
            names[contrib.lower().replace(" ", "").replace("_", "").rstrip("=")].add(contrib)
            if contrib.lower() in login_to_name:
                by_login[contrib] += 1
        meta = D.LICENSE_META.get(lic, {})
        basis = e.get("basis") or ""
        dist = e.get("distribution") or ""
        if basis and basis not in D.BASIS_VALUES:
            errors.append(f"unknown basis {basis!r}: {f}")
        if dist and dist not in D.DISTRIBUTION_VALUES:
            errors.append(f"unknown distribution {dist!r}: {f}")
        if meta.get("draft") and public:
            errors.append(f"draft license {lic} recorded in a public ledger: {f}")
        if meta.get("release_only") or dist == "release-only":
            if public:
                errors.append(f"release-only row in a public ledger (run `ledger move`): {f}")
            if dist != "release-only":
                errors.append(f"store license without distribution=release-only: {f}")
        if basis == "purchased" or meta.get("release_only"):
            srcs = e.get("sources")
            if not isinstance(srcs, list) or not srcs:
                errors.append(f"purchased row without sources: {f}")
            else:
                for s in srcs:
                    if not isinstance(s, dict) or not s.get("store"):
                        errors.append(f"source entry without store: {f}")
                    elif not s.get("item"):
                        warns.append(f"source without item id: {f}")
        if meta.get("requires_rights_holder") and not e.get("rights_holder"):
            errors.append(f"{lic} row without rights_holder: {f}")
        rf = set(fbf.get(f, {}).get("flags", [])) & store_flags
        if rf and not any(D.license_for_flag(x) == lic for x in rf):
            flagged_unmoved += 1
    if flagged_unmoved:
        warns.append(f"{flagged_unmoved} row(s) carry a store flag without the matching store license; run `ledger move`")
    for k, v in names.items():
        if len(v) > 1:
            warns.append(f"contributor spelling variants: {sorted(v)}")
    for login, n in sorted(by_login.items()):
        warns.append(f"{n} row(s) credit the login {login!r}; contributors.json names them {login_to_name[login.lower()]!r}")
    if fp.exists():
        if not isinstance(flags, dict) or not isinstance(flags.get("files"), list):
            errors.append("flags file has unexpected shape")
        else:
            for v in flags["files"]:
                f = v.get("file", "")
                if not (repo / f).exists():
                    warns.append(f"flag entry for missing file: {f}")
    for w in warns:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    print(f"lint: {len(assigns)} rows, {len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if errors else 0


def cmd_rebase(repo: Path, old: str, new: str, apply: bool, drop_missing: bool) -> int:
    ap, sp, fp = _paths(repo)
    assigns = _read_json(ap, [])
    sigs = _read_json(sp, [])
    flags = _read_json(fp, None)
    renames: dict[str, str] = {}
    deleted: set[str] = set()
    for line in _git(repo, "diff", "--name-status", "-M50%", "--diff-filter=RD", old, new).splitlines():
        parts = line.split("\t")
        if parts[0].startswith("R") and len(parts) == 3:
            renames[parts[1]] = parts[2]
        elif parts[0] == "D" and len(parts) == 2:
            deleted.add(parts[1])
    by_file = {e["file"]: e for e in assigns}
    moved, dropped, collided = [], [], []
    for e in list(assigns):
        f = e["file"]
        if f in renames:
            dst = renames[f]
            if dst in by_file and by_file[dst] is not e:
                collided.append((f, dst))
                assigns.remove(e)
            else:
                e["file"] = dst
                moved.append((f, dst))
        elif f in deleted:
            dropped.append(f)
            assigns.remove(e)
    still_missing = [e["file"] for e in assigns if not (repo / e["file"]).exists()]
    if drop_missing and still_missing:
        gone = set(still_missing)
        assigns[:] = [e for e in assigns if e["file"] not in gone]
        deleted |= gone
        dropped.extend(sorted(gone))
    for s in sigs:
        if s.get("file") in renames:
            s["file"] = renames[s["file"]]
    if isinstance(flags, dict):
        kept = []
        for v in flags.get("files", []):
            f = v.get("file", "")
            if f in renames:
                v["file"] = renames[f]
            if f in deleted or (drop_missing and not (repo / v["file"]).exists()):
                continue
            kept.append(v)
        flags["files"] = kept
    print(f"renames in range: {len(renames)}, deletions: {len(deleted)}")
    print(f"ledger rows moved: {len(moved)}, dropped (deleted upstream): {len(dropped)}, dropped (rename onto existing row): {len(collided)}")
    for a, b in moved[:20]:
        print(f"  {a} -> {b}")
    for f in dropped[:20]:
        print(f"  - {f}")
    if still_missing:
        print(f"{'dropped as' if drop_missing else 'still'} missing on disk after rebase: {len(still_missing)}")
        for f in still_missing[:20]:
            print(f"  ? {f}")
    if not apply:
        print("dry run; pass --apply to write")
        return 0
    assigns.sort(key=lambda x: x["file"])
    _write_json(ap, assigns)
    if sp.exists():
        _write_json(sp, sigs)
    if isinstance(flags, dict):
        _write_json(fp, flags)
    written = D.regenerate_manifests(repo, assigns)
    print(f"written; manifests regenerated: {len(written)}")
    return 0


# Words shown after /confirm-license; licensing.js keeps the same pair.
CONSENT_ALIAS = {"LicenseRef-BAR-CLA-1.0": "cla", "CC-BY-SA-4.0": "cc-by-sa"}


def _consent_choice_lines(repo: Path) -> list[str]:
    """One command line per PR choice, worded as the bot's own reply."""
    cfg = D.load_config(repo)
    licenses = cfg.get("licenses") or {}
    lines = []
    for lid in cfg.get("pr_choices") or list(CONSENT_ALIAS):
        meta = licenses.get(lid) or {}
        doc = f" ([details](../blob/HEAD/{meta['doc']}))" if meta.get("doc") else ""
        blurb = meta.get("consent_blurb") or meta.get("blurb") or ""
        lines.append(f"- `/confirm-license {CONSENT_ALIAS.get(lid, lid)}` — **{meta.get('name') or lid}**: {blurb}{doc}")
    return lines


def cmd_consent_body(repo: Path, contributors: list[str], github: str, out: Path | None) -> int:
    """Print the body for a consent issue addressed to one contributor."""
    ap, _, _ = _paths(repo)
    assigns = _read_json(ap, [])
    names = {c.lower() for c in contributors}
    rows = sorted((e for e in assigns if not e.get("license") and (e.get("contributor") or "").lower() in names),
                  key=lambda e: e["file"])
    if not rows:
        print(f"no awaiting rows for {contributors}", file=sys.stderr)
        return 1
    by_top: dict[str, list[str]] = collections.defaultdict(list)
    for e in rows:
        by_top[D.art_top_for_path(e["file"]) or "(other)"].append(e["file"])
    who = f"@{github}" if github else " / ".join(contributors)
    lines = [
        f"Hi {who},",
        "",
        f"Beyond All Reason is recording a license for every art and audio asset in the game. "
        f"The ledger credits **{' / '.join(contributors)}** as the author of **{len(rows)} file(s)** listed below, "
        "and we need your choice before those files can carry a license.",
        "",
        "Reply to this issue with **one** of these commands:",
        "",
        *_consent_choice_lines(repo),
        "",
        "Either choice covers all of your past contributions listed here. If some files are not yours, "
        "or were built from purchased or third-party material, say so in a comment instead of confirming.",
        "",
        "If your contributions are recorded under a different name than your GitHub login, a maintainer "
        "will link them before you answer.",
        "",
    ]
    for top in sorted(by_top):
        files = by_top[top]
        lines.append(f"<details><summary><code>{top}/</code> — {len(files)} file(s)</summary>")
        lines.append("")
        lines.extend(f"- `{f}`" for f in files)
        lines.append("")
        lines.append("</details>")
        lines.append("")
    text = "\n".join(lines)
    if out:
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {out} ({len(rows)} files)")
    else:
        print(text)
    return 0


def cmd_apply_consent(repo: Path, contributors: list[str], license_id: str, ref: str, source: str, apply: bool) -> int:
    """Set one license on every awaiting row of the named contributor(s), as Job 8 does on an issue."""
    if license_id not in D.KNOWN_LICENSES:
        print(f"unknown license {license_id!r}", file=sys.stderr)
        return 2
    ap, _, _ = _paths(repo)
    assigns = _read_json(ap, [])
    names = {c.lower() for c in contributors}
    now = _now()
    by_top: collections.Counter = collections.Counter()
    for e in assigns:
        if e.get("license") or (e.get("contributor") or "").lower() not in names:
            continue
        e["license"] = license_id
        e["pr"] = ref
        e["date"] = now
        e["source"] = source
        e.pop("status", None)
        by_top[D.art_top_for_path(e["file"]) or "(other)"] += 1
    total = sum(by_top.values())
    print(f"{license_id} for {' / '.join(contributors)}: {total} row(s)")
    for top, n in sorted(by_top.items()):
        print(f"  {top}/ {n}")
    if not total:
        return 1
    if not apply:
        print("dry run; pass --apply to write")
        return 0
    _write_json(ap, assigns)
    written = D.regenerate_manifests(repo, assigns)
    print(f"written; manifests regenerated: {len(written)}")
    return 0


def _target_license(row: dict, row_flags: list[str], to_license: str) -> str:
    if to_license:
        return to_license
    for fl in row_flags:
        lic = D.license_for_flag(fl)
        if lic:
            return lic
    return row.get("license") or ""


def cmd_move(src: Path, dst: Path, flags_sel: list[str], lic_sel: list[str], list_file: Path | None,
             to_license: str, keep_files: bool, apply: bool, moved_from: str = "") -> int:
    """Move rows, flags and files from *src* to *dst*; store flags become the license."""
    if to_license and to_license not in D.KNOWN_LICENSES:
        print(f"unknown license {to_license!r}", file=sys.stderr)
        return 2
    s_ap, _, s_fp = _paths(src)
    d_ap, _, d_fp = _paths(dst)
    s_assigns = _read_json(s_ap, [])
    s_flags = _read_json(s_fp, None)
    d_assigns = _read_json(d_ap, [])
    d_flags = _read_json(d_fp, None) or {"flag_defs": [], "files": []}
    s_fbf = _flags_by_file(s_flags)
    d_by_file = {e["file"]: e for e in d_assigns}
    wanted: set[str] = set()
    if list_file:
        wanted |= {ln.strip() for ln in list_file.read_text(encoding="utf-8-sig").splitlines() if ln.strip() and not ln.startswith("#")}
    for e in s_assigns:
        f = e["file"]
        fl = set(s_fbf.get(f, {}).get("flags", []))
        if (flags_sel and fl & set(flags_sel)) or (lic_sel and e.get("license") in lic_sel):
            wanted.add(f)
    rows = [e for e in s_assigns if e["file"] in wanted]
    missing_rows = sorted(wanted - {e["file"] for e in rows})
    dst_cfg = D.load_config(dst)
    if (dst_cfg.get("distribution") or "public") != "release-only":
        print("WARN  destination licensing.json is not distribution=release-only")
    slug = moved_from or _repo_slug(src)
    problems: list[str] = []
    plan: list[tuple[dict, dict, dict | None]] = []
    store_flags = D.store_flags()
    by_lic: collections.Counter = collections.Counter()
    for e in rows:
        f = e["file"]
        entry = s_fbf.get(f)
        row_flags = list(entry.get("flags", [])) if entry else []
        lic = _target_license(e, row_flags, to_license)
        if not lic:
            problems.append(f"no target license (pass --to-license): {f}")
            continue
        if not (src / f).exists():
            problems.append(f"missing on disk in source: {f}")
            continue
        if f in d_by_file:
            problems.append(f"already in destination ledger: {f}")
            continue
        meta = D.LICENSE_META.get(lic, {})
        new = dict(e)
        new["license"] = lic
        new.pop("status", None)
        if meta.get("release_only"):
            new["basis"] = new.get("basis") or "purchased"
            new["distribution"] = "release-only"
            if not new.get("sources"):
                new["sources"] = [{"store": meta.get("store", "")}]
        new["moved_from"] = slug
        new_entry = None
        if entry:
            kept = [x for x in row_flags if x not in store_flags]
            if kept or entry.get("comments"):
                new_entry = {"file": f, "flags": kept, "comments": list(entry.get("comments", []))}
        plan.append((e, new, new_entry))
        by_lic[lic] += 1
    print(f"selected {len(wanted)} path(s): {len(plan)} movable, {len(problems)} problem(s), {len(missing_rows)} not in source ledger")
    for lic, n in by_lic.most_common():
        print(f"  {n:5d}  {lic}")
    for p in problems[:30]:
        print("  !", p)
    for f in missing_rows[:10]:
        print("  ?", f)
    if problems or not plan:
        print("nothing written" if not plan else "fix the problems above first; nothing written")
        return 1 if problems else 0
    if not apply:
        print("dry run; pass --apply to move")
        return 0
    moved_files = set()
    for old, new, new_entry in plan:
        f = old["file"]
        target = dst / f
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / f, target)
        d_assigns.append(new)
        if new_entry:
            d_flags.setdefault("files", []).append(new_entry)
        moved_files.add(f)
    s_assigns[:] = [e for e in s_assigns if e["file"] not in moved_files]
    if isinstance(s_flags, dict):
        s_flags["files"] = [v for v in s_flags.get("files", []) if v.get("file") not in moved_files]
    have = {d.get("id") for d in d_flags.get("flag_defs", []) if isinstance(d, dict)}
    for d in (s_flags or {}).get("flag_defs", []) if isinstance(s_flags, dict) else []:
        if isinstance(d, dict) and d.get("id") not in have and d.get("id") not in store_flags:
            d_flags.setdefault("flag_defs", []).append(d)
    d_assigns.sort(key=lambda x: x["file"])
    d_flags["files"] = sorted(d_flags.get("files", []), key=lambda x: x.get("file", ""))
    _write_json(d_ap, d_assigns)
    _write_json(d_fp, d_flags)
    _write_json(s_ap, s_assigns)
    if isinstance(s_flags, dict):
        _write_json(s_fp, s_flags)
    if not keep_files:
        for f in sorted(moved_files):
            if not _git(src, "rm", "-q", "--", f, check=False) and (src / f).exists():
                try:
                    (src / f).unlink()
                except OSError:
                    pass
    D.apply_config(dst_cfg)
    dw = D.regenerate_manifests(dst, d_assigns)
    D.apply_config(D.load_config(src))
    sw = D.regenerate_manifests(src, s_assigns)
    print(f"moved {len(moved_files)} file(s); manifests regenerated: {len(sw)} in source, {len(dw)} in destination")
    return 0


def _code_globs(repo: Path) -> list[str]:
    """Globs for everything outside the art directories, expanding dirs that nest one."""
    art = [d.strip("/") for d in D.ART_TOP_DIRS]
    skip = {".git"}

    def nests_art(rel: str) -> bool:
        return any(a.startswith(rel + "/") for a in art)

    def expand(rel: str) -> list[str]:
        out = []
        base = repo / rel if rel else repo
        for p in sorted(base.iterdir()):
            r = f"{rel}/{p.name}" if rel else p.name
            if p.name in skip or r in art:
                continue
            if p.is_dir():
                out += expand(r) if nests_art(r) else [f"{r}/**"]
            else:
                out.append(r)
        return out

    globs = expand("")
    # Code, docs and notes that sit inside art directories are project files too.
    exts = sorted({e.strip(".") for e in list(D.CODE_EXT) + list(D.DOC_EXT)} | {"txt", "json", "toml", "yml"})
    for a in art:
        if (repo / a).exists():
            globs += [f"{a}/**/*.{e}" for e in exts]
    return globs


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cmd_reuse(repo: Path, out: Path | None) -> int:
    """Write REUSE.toml: GPL default for code, one annotation per (license, contributor) from the ledger."""
    ap, _, _ = _paths(repo)
    assigns = _read_json(ap, [])
    cfg = D.load_config(repo)
    meta = cfg.get("reuse", {}) if isinstance(cfg.get("reuse"), dict) else {}
    code_license = meta.get("code_license", "GPL-2.0-or-later")
    code_holder = meta.get("code_copyright", "Beyond All Reason contributors")
    groups: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for e in assigns:
        lic = e.get("license") or ""
        who = (e.get("contributor") or "").strip()
        holder = "NOASSERTION" if D.is_public_domain_contributor(who) or not who else who
        groups[(lic, holder)].append(e["file"])
    lines = ["# Generated by tools/ledger.py reuse from .github/file-license-assignments.json. Do not edit by hand.",
             "version = 1"]
    for k, v in (("SPDX-PackageName", meta.get("package_name")), ("SPDX-PackageSupplier", meta.get("package_supplier")),
                 ("SPDX-PackageDownloadLocation", meta.get("download_location"))):
        if v:
            lines.append(f"{k} = {_toml_str(v)}")
    lines += ["", "[[annotations]]", "path = [" + ", ".join(_toml_str(g) for g in _code_globs(repo)) + "]",
              'precedence = "closest"', f"SPDX-FileCopyrightText = {_toml_str(code_holder)}",
              f"SPDX-License-Identifier = {_toml_str(code_license)}"]
    for extra in meta.get("annotations", []) if isinstance(meta.get("annotations"), list) else []:
        if not isinstance(extra, dict) or not extra.get("path"):
            continue
        paths = extra["path"] if isinstance(extra["path"], list) else [extra["path"]]
        lines += ["", "[[annotations]]", "path = [" + ", ".join(_toml_str(p) for p in paths) + "]",
                  f"precedence = {_toml_str(extra.get('precedence', 'override'))}",
                  f"SPDX-FileCopyrightText = {_toml_str(extra.get('copyright', 'NOASSERTION'))}"]
        if extra.get("license"):
            lines.append(f"SPDX-License-Identifier = {_toml_str(extra['license'])}")
    for (lic, holder), files in sorted(groups.items(), key=lambda kv: (kv[0][0] or "~", kv[0][1])):
        lines += ["", "[[annotations]]", "path = [" + ", ".join(_toml_str(f) for f in sorted(files)) + "]",
                  'precedence = "override"', f"SPDX-FileCopyrightText = {_toml_str(holder)}"]
        if lic:
            lines.append(f"SPDX-License-Identifier = {_toml_str(lic)}")
    text = "\n".join(lines) + "\n"
    target = out or (repo / "REUSE.toml")
    target.write_text(text, encoding="utf-8", newline="\n")
    awaiting = sum(len(f) for (lic, _), f in groups.items() if not lic)
    print(f"wrote {target} ({len(groups)} ledger annotations, {awaiting} awaiting files listed without a license)")
    used = {lic for lic, _ in groups if lic} | {code_license} | {x.get("license") for x in meta.get("annotations", []) if isinstance(x, dict)}
    lic_dir = repo / "LICENSES"
    if lic_dir.exists():
        unused = sorted(p.stem for p in lic_dir.glob("*.txt") if p.stem not in used)
        if unused:
            print("LICENSES/ texts no ledger row uses (fine when code headers use them; `reuse lint` names the truly unused): " + ", ".join(unused))
    return 0


def cmd_manifests(repo: Path) -> int:
    ap, _, _ = _paths(repo)
    written = D.regenerate_manifests(repo, _read_json(ap, []))
    for w in written:
        print(w.relative_to(repo).as_posix())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("stats", "lint", "manifests"):
        sub.add_parser(name).add_argument("repo")
    rb = sub.add_parser("rebase")
    rb.add_argument("repo")
    rb.add_argument("--old", required=True)
    rb.add_argument("--new", required=True)
    rb.add_argument("--apply", action="store_true")
    rb.add_argument("--drop-missing", action="store_true", help="also drop rows whose file is absent after renames")
    cb = sub.add_parser("consent-body", help="issue body asking one contributor for their license choice")
    cb.add_argument("repo")
    cb.add_argument("--contributor", action="append", required=True, help="ledger name; repeat for spelling variants")
    cb.add_argument("--github", default="", help="GitHub login to address")
    cb.add_argument("--out", default="", help="write to this file instead of stdout")
    ac = sub.add_parser("apply-consent", help="set a license on a contributor's awaiting rows")
    ac.add_argument("repo")
    ac.add_argument("--contributor", action="append", required=True)
    ac.add_argument("--license", required=True)
    ac.add_argument("--ref", default="manual-audit", help="issue number or other reference stored in pr")
    ac.add_argument("--source", default="manual-audit")
    ac.add_argument("--apply", action="store_true")
    ru = sub.add_parser("reuse", help="write REUSE.toml from the ledger")
    ru.add_argument("repo")
    ru.add_argument("--out", default="", help="write here instead of <repo>/REUSE.toml")
    mv = sub.add_parser("move", help="move rows, flags and files to another repository")
    mv.add_argument("src")
    mv.add_argument("dst")
    mv.add_argument("--flag", action="append", default=[], help="select rows carrying this flag; repeatable")
    mv.add_argument("--license", action="append", default=[], help="select rows under this license; repeatable")
    mv.add_argument("--list", default="", help="file with one repo-relative path per line")
    mv.add_argument("--to-license", default="", help="license for the moved rows (default: from the store flag)")
    mv.add_argument("--keep-files", action="store_true", help="copy files instead of removing them from the source")
    mv.add_argument("--moved-from", default="", help="provenance slug for the moved rows (default: source origin)")
    mv.add_argument("--apply", action="store_true")
    ns = ap.parse_args(argv)
    repo = Path(ns.src if ns.cmd == "move" else ns.repo).resolve()
    D.apply_config(D.load_config(repo))
    if ns.cmd == "stats":
        return cmd_stats(repo)
    if ns.cmd == "lint":
        return cmd_lint(repo)
    if ns.cmd == "rebase":
        return cmd_rebase(repo, ns.old, ns.new, ns.apply, ns.drop_missing)
    if ns.cmd == "manifests":
        return cmd_manifests(repo)
    if ns.cmd == "reuse":
        return cmd_reuse(repo, Path(ns.out) if ns.out else None)
    if ns.cmd == "consent-body":
        return cmd_consent_body(repo, ns.contributor, ns.github, Path(ns.out) if ns.out else None)
    if ns.cmd == "apply-consent":
        return cmd_apply_consent(repo, ns.contributor, ns.license, ns.ref, ns.source, ns.apply)
    if ns.cmd == "move":
        return cmd_move(repo, Path(ns.dst).resolve(), ns.flag, ns.license,
                        Path(ns.list) if ns.list else None, ns.to_license, ns.keep_files, ns.apply, ns.moved_from)
    return 2


if __name__ == "__main__":
    sys.exit(main())
