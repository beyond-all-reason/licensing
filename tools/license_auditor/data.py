"""Data loading and persistence for the BAR License Auditor."""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Art directories in browser display order. A nested entry ("luaui/images")
# names the art subfolder of a code directory so its sources stay out.
ART_TOP_DIRS = [
    "anims",
    "bitmaps",
    "fonts",
    "icons",
    "modelmaterials",
    "modelmaterials_gl4",
    "music",
    "objects3d",
    "sidepics",
    "sounds",
    "unitpics",
    "unittextures",
    # Art nested inside otherwise-code directories.
    "luaui/images",
    "luaui/sounds",
    "luarules/images",
    "singleplayer/scenarios",
]

# Source/code extensions that are never art and are skipped during the walk.
# .lua/.py matter now that art-bearing code dirs are scanned (luaui/images holds
# a couple of tooling scripts; singleplayer/scenarios is half scenario .lua).
CODE_EXT = {".glsl", ".frag", ".vert", ".glslf", ".glslv", ".bos", ".cob", ".lua", ".py"}

# Manifests, license notices and docs sit beside assets but are not assets.
DOC_EXT = {".md", ".ipynb"}
NOTICE_STEM = re.compile(r"licen[cs]e|^ofl(?:$|[-_])", re.IGNORECASE)

CONFIG_FILENAME = ".github/licensing.json"


def is_non_asset(fname: str) -> bool:
    """True for code, docs and license notices that must stay out of the ledger."""
    stem, ext = os.path.splitext(fname)
    ext = ext.lower()
    if ext in CODE_EXT or ext in DOC_EXT:
        return True
    return ext == ".txt" and bool(NOTICE_STEM.search(stem))


def load_config(repo_root: Path) -> dict:
    """Read .github/licensing.json; missing file means built-in defaults."""
    try:
        return json.loads((repo_root / CONFIG_FILENAME).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def apply_config(cfg: dict) -> None:
    """Rebuild the module registries in place from a licensing.json dict."""
    global NOTICE_STEM, REPO_DISTRIBUTION
    if cfg.get("art_dirs"):
        ART_TOP_DIRS[:] = [str(d).strip("/") for d in cfg["art_dirs"]]
    if cfg.get("code_extensions"):
        CODE_EXT.clear()
        CODE_EXT.update("." + e.lstrip(".").lower() for e in cfg["code_extensions"])
    if cfg.get("doc_extensions"):
        DOC_EXT.clear()
        DOC_EXT.update("." + e.lstrip(".").lower() for e in cfg["doc_extensions"])
    if cfg.get("notice_stem_pattern"):
        NOTICE_STEM = re.compile(cfg["notice_stem_pattern"], re.IGNORECASE)
    if cfg.get("licenses"):
        KNOWN_LICENSES[:] = list(cfg["licenses"].keys())
        LICENSE_META.clear()
        LICENSE_META.update({k: dict(v) for k, v in cfg["licenses"].items() if isinstance(v, dict)})
    REPO_DISTRIBUTION = str(cfg.get("distribution") or "public")

KNOWN_LICENSES = [
    "LicenseRef-BAR-CLA-1.0",
    "CC-BY-SA-4.0",
    "GPL-2.0-or-later",
    "MIT",
    "CC0-1.0",
    "OFL-1.1",
    "CC-BY-SA-3.0",
    "CC-BY-4.0",
    "CC-BY-NC-ND-4.0",
]

# Catalogue entries from licensing.json: release_only, store, flag, requires_rights_holder.
LICENSE_META: dict[str, dict] = {}
# "public" here, "release-only" in the private asset repository.
REPO_DISTRIBUTION = "public"
BASIS_VALUES = ("contributed", "purchased", "assignment", "in-house")
DISTRIBUTION_VALUES = ("public", "release-only")
# Ledger keys the auditor edits; other keys ride along in FileRecord.extra.
LEDGER_CORE_KEYS = {"file", "license", "contributor", "submitted_by", "pr", "date", "source", "status"}


def release_only_licenses() -> list[str]:
    return [k for k, m in LICENSE_META.items() if m.get("release_only")]


def license_for_flag(flag: str) -> str:
    """Store license whose catalogue entry names *flag*, else ""."""
    for k, m in LICENSE_META.items():
        if flag and m.get("flag") == flag:
            return k
    return ""


def owned_licenses() -> list[str]:
    return [k for k, m in LICENSE_META.items() if m.get("requires_rights_holder")]


def store_flags() -> set[str]:
    return {m["flag"] for m in LICENSE_META.values() if m.get("flag")}


# CC0 = public domain: there is no rights holder, so the Contributor cell
# gets this placeholder instead of the git/unitdef-derived author.
PUBLIC_DOMAIN_LICENSE = "CC0-1.0"
PUBLIC_DOMAIN_CONTRIBUTOR = "public domain"

# Flag registry, id -> (label, color). Custom defs from the Flag Wizard merge in
# via set_custom_flag_defs(); mutate these objects in place, other modules import them.
BUILTIN_FLAG_DEFS: list[tuple[str, str, str]] = [
    ("",                 "(none)",            "#5865f2"),
    ("unused",           "Unused (remove)",   "#a0a0a0"),  # mid grey
    ("unsure",           "Unsure",            "#e05c7a"),  # rose - needs a second opinion
    ("replace",          "Replace",           "#27ae60"),  # green - asset should be replaced
    ("floris-review",    "Floris - review",   "#e67e22"),
    ("beherith-review",  "Beherith - review", "#9b59b6"),
    ("icexuick-review",  "IceXuick - review", "#1abc9c"),
    ("ptaq-review",      "PtaQ - review",     "#f1c40f"),
]
CUSTOM_FLAG_DEFS: list[tuple[str, str, str]] = []
FLAG_DEFS: list[tuple[str, str, str]] = list(BUILTIN_FLAG_DEFS)
FLAG_VALUES: list[str] = [v for v, _l, _c in FLAG_DEFS]
FLAG_LABEL: dict[str, str] = {v: l for v, l, _c in FLAG_DEFS}
FLAG_COLOR: dict[str, str] = {v: c for v, _l, c in FLAG_DEFS}


def set_custom_flag_defs(defs: list[tuple[str, str, str]]) -> None:
    """Replace the custom flag defs and rebuild the live registry in place.

    Custom ids may not shadow built-in ids; duplicates/empties are dropped.
    """
    builtin_ids = {v for v, _l, _c in BUILTIN_FLAG_DEFS}
    seen: set[str] = set()
    cleaned: list[tuple[str, str, str]] = []
    for fv, fl, fc in defs or []:
        fv = (fv or "").strip()
        if not fv or fv in builtin_ids or fv in seen:
            continue
        seen.add(fv)
        cleaned.append((fv, (fl or fv).strip(), (fc or "#888888").strip()))
    CUSTOM_FLAG_DEFS[:] = cleaned
    FLAG_DEFS[:] = list(BUILTIN_FLAG_DEFS) + cleaned
    FLAG_VALUES[:] = [v for v, _l, _c in FLAG_DEFS]
    FLAG_LABEL.clear(); FLAG_LABEL.update({v: l for v, l, _c in FLAG_DEFS})
    FLAG_COLOR.clear(); FLAG_COLOR.update({v: c for v, _l, c in FLAG_DEFS})


def normalize_flags(flags) -> list[str]:
    """Registry order for known flags, unknown ones last; dupes/empties dropped."""
    order = {v: i for i, v in enumerate(FLAG_VALUES)}
    seen: set[str] = set()
    out = [f for f in (flags or []) if f and not (f in seen or seen.add(f))]
    out.sort(key=lambda f: order.get(f, len(order)))
    return out


def flags_display(flags) -> str:
    """Human-readable comma list for a record's flags (unknown ids shown raw)."""
    return ", ".join(FLAG_LABEL.get(f, f) for f in (flags or []))


COMMENTS_FLAGS_FILENAME = "license_comments_flags.json"


@dataclass
class FileRecord:
    path: str          # repo-relative, forward slashes, e.g. "unitpics/armcom.png"
    top_dir: str       # e.g. "unitpics"
    subpath: str       # relative to top_dir, e.g. "cor/armcom.png"
    status: str        # 'gap' | 'intent' | 'awaiting_license' | 'confirmed' | 'merged'
    license: str = ""
    contributor: str = ""
    proxy_submitter: str = ""
    pr: str = ""
    date: str = ""
    last_changed: str = ""   # most-recent git commit date (YYYY-MM-DD)
    git_author: str = ""     # GitHub handle inferred from first git commit
    pr_url: str = ""         # full GitHub PR URL if derivable
    commit_hash: str = ""    # full SHA of the first commit that added this file
    commit_url: str = ""     # https://github.com/owner/repo/commit/<hash>
    unitdef_author: str = "" # author from units/*.lua model field (objects3d only)
    author_confirmed: bool = False  # True once user explicitly confirms (Author button or inline-edit Enter)
    flags: list = field(default_factory=list, repr=False)  # flag ids (built-in + custom); [] = none
    comments: list = field(default_factory=list, repr=False)  # list[dict{author,date,text}]
    extra: dict = field(default_factory=dict, repr=False)  # rights_holder, basis, sources, distribution, ...
    flags_comments_dirty: bool = field(default=False, repr=False)  # separate from license-ledger dirty
    dirty: bool = field(default=False, repr=False)


def is_public_domain_contributor(value: str) -> bool:
    return (value or "").strip().lower() == PUBLIC_DOMAIN_CONTRIBUTOR


def default_contributor(rec: FileRecord) -> str:
    """Prefill for a record without a contributor: unitdef author → proxy submitter → git author."""
    return rec.unitdef_author or rec.proxy_submitter or rec.git_author or ""


def contributor_for_license(rec: FileRecord, lic: str, contributor: str) -> str:
    """Contributor value to store when *lic* is assigned to *rec*.

    *contributor* is what the caller would otherwise store.

    * CC0 (public domain): always the ``"public domain"`` placeholder — there is
      no rights holder to record, so any real or prefilled author is replaced.
    * any other license: *contributor* unchanged, except that a leftover
      placeholder from an earlier CC0 assignment reverts to the normal prefill
      (or to empty when the license itself is being cleared).
    """
    if lic == PUBLIC_DOMAIN_LICENSE:
        return PUBLIC_DOMAIN_CONTRIBUTOR
    if is_public_domain_contributor(contributor):
        return default_contributor(rec) if lic else ""
    return contributor


def _load_unitdef_authors(repo_root: Path) -> dict[str, str]:
    """Scan units/ directory for .lua unitdef files.

    Returns a mapping of lowercase model-basename (no extension) → author string
    extracted from the ``author = "..."`` field.  When no explicit ``model``
    field is present the Lua filename itself is used as the model-name key.
    """
    units_dir = repo_root / "units"
    if not units_dir.exists():
        return {}

    result: dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(units_dir):
        for fname in files:
            if not fname.lower().endswith(".lua"):
                continue
            fpath = Path(dirpath) / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            m_author = re.search(
                r'\bmodel_author\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE
            )
            if not m_author:
                # Fall back to generic author only when no model_author field exists
                m_author = re.search(
                    r'(?<![_\w])author\s*=\s*["\']([^"\']+)["\']', content, re.IGNORECASE
                )
            if not m_author:
                continue
            author = m_author.group(1).strip()
            if not author:
                continue

            # The lua filename keys its same-named s3o and always wins.
            lua_key = os.path.splitext(fname)[0].lower()
            if lua_key:
                result[lua_key] = author

            # Secondary index: assets referenced inside the lua get the same
            # author unless a lua filename already keys them.
            for m_ref in re.finditer(
                r'["\']([^"\']+\.(?:s3o|dds|bmp|png|tga))["\']',
                content, re.IGNORECASE,
            ):
                ref_key = os.path.splitext(os.path.basename(m_ref.group(1)))[0].lower()
                if ref_key and ref_key != lua_key and ref_key not in result:
                    result[ref_key] = author

    return result


# Suffixes appended to a unit's model basename for wreck / heap variants.
# Stripped before falling back to the parent unit's model_author.
_WRECK_SUFFIXES = ("_dead2", "_dead", "_heap", "_x")


def _strip_wreck_suffix(stem: str) -> str:
    for suf in _WRECK_SUFFIXES:
        if stem.endswith(suf):
            return stem[: -len(suf)]
    return stem


# Ground-decal textures are named <model>[_<w>_<h>]_aoplane[_scav].<ext>:
# unittextures/decals/armsilo_8_8_aoplane.dds for the live unit and
# unittextures/decals_features/armsilo_dead_8_8_aoplane.dds for its wreck.
_DECAL_TAIL_RE = re.compile(r"(?:_\d+_\d+)?_aoplane(?:_scav)?$")


def unit_key_candidates(stem: str) -> list[str]:
    """Lowercase unitdef lookup keys for an asset stem, most specific first.

    Peels the decal tail, then the wreck/heap suffix, so every artefact of a
    model resolves to the same ``units/<unit>.lua``::

        armsilo_dead_8_8_aoplane -> [armsilo_dead_8_8_aoplane, armsilo_dead, armsilo]
        cordrag_aoplane_scav     -> [cordrag_aoplane_scav, cordrag]
        armsilo_dead             -> [armsilo_dead, armsilo]
    """
    stem = stem.lower()
    keys = [stem]
    base = _DECAL_TAIL_RE.sub("", stem)
    if base != stem:
        keys.append(base)
    stripped = _strip_wreck_suffix(base)
    if stripped != base:
        keys.append(stripped)
    return keys


def load_comments_flags(repo_root: Path) -> tuple[list[tuple[str, str, str]], dict[str, dict]]:
    """Load license_comments_flags.json from the repo root.

    Returns ``(custom_flag_defs, mapping)`` with mapping
    ``file_path -> {"flags": list[str], "comments": list[dict]}``.
    Accepts the legacy bare-list format (single ``"flag"`` per entry) as well
    as the current ``{"flag_defs": [...], "files": [...]}`` shape.
    Missing or malformed file -> ``([], {})``.
    """
    fc_path = repo_root / COMMENTS_FLAGS_FILENAME
    try:
        raw = json.loads(fc_path.read_text(encoding="utf-8"))
    except Exception:
        return [], {}
    defs: list[tuple[str, str, str]] = []
    entries = raw
    if isinstance(raw, dict):
        for d in raw.get("flag_defs", []) or []:
            if isinstance(d, dict) and d.get("id"):
                defs.append((d["id"], d.get("label", d["id"]), d.get("color", "#888888")))
        entries = raw.get("files", []) or []
    out: dict[str, dict] = {}
    if isinstance(entries, list):
        for entry in entries:
            fp = entry.get("file")
            if not fp:
                continue
            flags = entry.get("flags")
            if flags is None:  # legacy single-flag entry
                flags = [entry["flag"]] if entry.get("flag") else []
            out[fp] = {
                "flags": [f for f in flags if f],
                "comments": entry.get("comments", []) or [],
            }
    return defs, out


def load_repo(repo_root: Path) -> tuple[list[FileRecord], list[dict], list[dict], dict[str, dict]]:
    """Walk ART_TOP_DIRS and cross-reference with both license ledgers.

    Returns:
        records         – one FileRecord per art file found on disk
        raw_sigs        – raw list from cla-signatures.json (for re-save)
        raw_assigns     – raw list from file-license-assignments.json (for re-save)
        raw_flags_comments – dict[file_path → {flag, comments}] from
                            license_comments_flags.json
    """
    apply_config(load_config(repo_root))
    sigs_path = repo_root / ".github" / "cla-signatures.json"
    assigns_path = repo_root / ".github" / "file-license-assignments.json"

    raw_sigs: list[dict] = []
    try:
        raw_sigs = json.loads(sigs_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    raw_assigns: list[dict] = []
    try:
        raw_assigns = json.loads(assigns_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    # Build fast lookup dicts
    intent_by_path: dict[str, dict] = {}
    for s in raw_sigs:
        if s.get("status") == "intent":
            intent_by_path[s["file"]] = s

    confirmed_by_path: dict[str, dict] = {}
    for a in raw_assigns:
        confirmed_by_path[a["file"]] = a

    records: list[FileRecord] = []
    root_str = str(repo_root)

    for top in ART_TOP_DIRS:
        top_path = repo_root / top
        if not top_path.exists():
            continue
        top_str = str(top_path)

        for dirpath, dirs, files in os.walk(top_path):
            dirs.sort()
            for fname in sorted(files):
                if is_non_asset(fname):
                    continue

                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, root_str).replace("\\", "/")
                sub = os.path.relpath(full, top_str).replace("\\", "/")

                if rel in confirmed_by_path:
                    a = confirmed_by_path[rel]
                    # "awaiting_license": contributor known, licence not chosen. Older
                    # rows with both fields and no status load as confirmed.
                    a_status = a.get("status") or ""
                    a_license = a.get("license", "") or ""
                    a_contrib = a.get("contributor", "") or ""
                    if a_status == "awaiting_license" or (a_contrib and not a_license):
                        rec_status = "awaiting_license"
                    else:
                        rec_status = "confirmed"
                    rec = FileRecord(
                        path=rel,
                        top_dir=top,
                        subpath=sub,
                        status=rec_status,
                        license=a_license,
                        contributor=a_contrib,
                        pr=str(a.get("pr", "")),
                        date=(a.get("date", "") or "")[:10],
                        author_confirmed=bool(a_contrib),
                        extra={k: v for k, v in a.items() if k not in LEDGER_CORE_KEYS},
                    )
                elif rel in intent_by_path:
                    s = intent_by_path[rel]
                    rec = FileRecord(
                        path=rel,
                        top_dir=top,
                        subpath=sub,
                        status="intent",
                        license=s.get("license", ""),
                        contributor=s.get("github_username", ""),
                        proxy_submitter=s.get("proxy_submitter", ""),
                        pr=str(s.get("pr", "")),
                        date=(s.get("signed_at", "") or "")[:10],
                        author_confirmed=bool(s.get("github_username")),
                    )
                else:
                    rec = FileRecord(path=rel, top_dir=top, subpath=sub, status="gap")

                records.append(rec)

    # Correlate art to unitdefs (model, wrecks, unitpic, aoplane, tracks, decals)
    # and prefill the contributor of gap rows; the user confirms it.
    unitdef_authors = _load_unitdef_authors(repo_root)
    if unitdef_authors:
        correlated_dirs = ("objects3d", "unitpics", "unittextures")
        for rec in records:
            if rec.top_dir not in correlated_dirs:
                continue
            stem = os.path.splitext(os.path.basename(rec.path))[0]
            ua = ""
            for key in unit_key_candidates(stem):
                ua = unitdef_authors.get(key, "")
                if ua:
                    break
            if ua:
                rec.unitdef_author = ua
                if not rec.contributor and rec.status == "gap":
                    rec.contributor = ua
                    rec.author_confirmed = False

    # Merge custom flag defs + flags + comments from license_comments_flags.json
    custom_defs, raw_flags_comments = load_comments_flags(repo_root)
    set_custom_flag_defs(custom_defs)
    if raw_flags_comments:
        for rec in records:
            fc = raw_flags_comments.get(rec.path)
            if not fc:
                continue
            rec.flags = normalize_flags(fc.get("flags"))
            rec.comments = list(fc.get("comments", []) or [])

    return records, raw_sigs, raw_assigns, raw_flags_comments


# ---------------------------------------------------------------------------
# Git info enrichment
# ---------------------------------------------------------------------------

def _get_repo_slug(repo_root: Path) -> str:
    """Return 'owner/repo' for the upstream GitHub repo, or ''.

    When the local clone is a fork, PR numbers and commit SHAs reference
    the upstream repo (where the PRs were actually opened). Prefer the
    ``upstream`` remote if present, then fall back to ``origin``. As a
    last-resort safety net, if the repo basename is ``Beyond-All-Reason``
    (regardless of owner), rewrite to the canonical
    ``beyond-all-reason/Beyond-All-Reason`` so links resolve correctly
    on personal forks even without an upstream remote configured.
    """
    def _slug_from_remote(remote: str) -> str:
        try:
            r = subprocess.run(
                ["git", "remote", "get-url", remote],
                capture_output=True, text=True, cwd=repo_root, timeout=5,
            )
            url = r.stdout.strip()
            if not url:
                return ""
            m = re.match(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?$", url)
            if m:
                return m.group(1)
            m = re.match(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?$", url)
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""

    for remote in ("upstream", "origin"):
        slug = _slug_from_remote(remote)
        if not slug:
            continue
        owner, repo = slug.split("/", 1)
        if repo.lower() == "beyond-all-reason" and owner.lower() != "beyond-all-reason":
            # Fork of BAR — rewrite to canonical upstream.
            return "beyond-all-reason/Beyond-All-Reason"
        return slug
    return ""


def _get_pr_base_url(repo_root: Path) -> str:
    """Return 'https://github.com/owner/repo/pull/' or ''."""
    slug = _get_repo_slug(repo_root)
    return f"https://github.com/{slug}/pull/" if slug else ""


def _extract_pr_num(subject: str) -> str:
    m = re.search(r"#(\d+)", subject)
    return m.group(1) if m else ""


def _github_handle(name: str, email: str) -> str:
    """Extract GitHub username from noreply email, fallback to display name."""
    m = re.match(r"\d+\+(.+)@users\.noreply\.github\.com", email)
    if m:
        return m.group(1)
    return name


def fetch_git_info(repo_root: Path, records: list[FileRecord]) -> None:
    """Populate git_author, last_changed, pr, pr_url, and date on records
    via a single ``git log --name-status`` pass.  Non-fatal: any error is
    silently ignored so the auditor still loads.
    """
    if not records:
        return

    pr_base = _get_pr_base_url(repo_root)
    path_map: dict[str, FileRecord] = {r.path: r for r in records}

    art_dirs = list({r.top_dir + "/" for r in records})

    try:
        result = subprocess.run(
            [
                "git", "log",
                "--format=>>>COMMIT<<<|%an|%ae|%ad|%H|%s",
                "--name-status",
                "--date=short",
                "--diff-filter=ACMR",
                "--",
            ] + art_dirs,
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=120,
        )
    except Exception:
        return

    # file_path -> list of (author_handle, date, pr_num, commit_hash)  reverse-chrono
    file_history: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    # PR numbers that have a merge commit (subject starts with "Merge pull request #")
    merged_pr_nums: set[str] = set()

    cur_author = ""
    cur_date = ""
    cur_pr = ""
    cur_hash = ""

    commit_base = pr_base.replace("/pull/", "/commit/") if pr_base else ""

    for line in result.stdout.splitlines():
        if line.startswith(">>>COMMIT<<<|"):
            parts = line.split("|", 5)
            if len(parts) >= 6:
                name, email, date, cur_hash, subj = parts[1], parts[2], parts[3], parts[4], parts[5]
                cur_author = _github_handle(name, email)
                cur_date = date
                cur_pr = _extract_pr_num(subj)
                # Detect GitHub merge commits
                if subj.startswith("Merge pull request #") or subj.startswith("Merged PR #"):
                    pr_num = _extract_pr_num(subj)
                    if pr_num:
                        merged_pr_nums.add(pr_num)
        elif line and line[0] in "ACMRD" and "\t" in line:
            # name-status: "M\tpath"  or  "R100\told\tnew"
            cols = line.split("\t")
            fpath = cols[-1].replace("\\", "/")
            if fpath in path_map:
                file_history[fpath].append((cur_author, cur_date, cur_pr, cur_hash))

    for fpath, history in file_history.items():
        rec = path_map[fpath]
        most_recent = history[0]   # git log is reverse-chron
        oldest = history[-1]

        rec.last_changed = most_recent[1]

        if not rec.git_author:
            rec.git_author = oldest[0]   # who first added the file

        # Fill pr / date from git only when JSON left them blank
        if not rec.pr and oldest[2]:
            rec.pr = oldest[2]
        if not rec.date and oldest[1]:
            rec.date = oldest[1]

        # Always try to build the PR URL
        pr_num = rec.pr
        if pr_base and pr_num and pr_num.isdigit():
            rec.pr_url = pr_base + pr_num

        # Populate commit hash/url from the oldest (first-add) commit
        first_hash = oldest[3] if len(oldest) > 3 else ""
        if first_hash:
            rec.commit_hash = first_hash
            if commit_base:
                rec.commit_url = commit_base + first_hash

        # Upgrade confirmed → merged when the PR was merged
        if rec.status == "confirmed" and pr_num and pr_num in merged_pr_nums:
            rec.status = "merged"


def _merged_row(old: dict | None, rec: FileRecord) -> dict:
    """Existing row with its key order; extension keys come from rec.extra."""
    row = {k: v for k, v in (old or {}).items() if k in LEDGER_CORE_KEYS}
    row.setdefault("file", rec.path)
    row.update(rec.extra or {})
    return row


def save_assignments(
    repo_root: Path,
    dirty_records: list[FileRecord],
    raw_assigns: list[dict],
) -> int:
    """Persist dirty confirmed records to file-license-assignments.json.

    Confirmed dirty records are upserted; gap dirty records are removed
    from the ledger (maintainer explicitly cleared them).
    Returns the number of entries written/removed.
    """
    existing: dict[str, dict] = {a["file"]: a for a in raw_assigns}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    changed = 0

    for rec in dirty_records:
        if not rec.dirty:
            continue
        if rec.status == "confirmed":
            if not rec.license or not rec.contributor:
                # Skip incomplete records; caller should validate before calling
                continue
            row = _merged_row(existing.get(rec.path), rec)
            row.pop("status", None)
            row.update({"license": rec.license, "contributor": rec.contributor,
                        "pr": rec.pr or "manual-audit", "date": now, "source": "manual-audit"})
            existing[rec.path] = row
            changed += 1
        elif rec.status == "awaiting_license":
            if not rec.contributor:
                # Cannot persist awaiting_license without an author
                continue
            row = _merged_row(existing.get(rec.path), rec)
            row.update({"status": "awaiting_license", "license": "", "contributor": rec.contributor,
                        "pr": rec.pr or "manual-audit", "date": now, "source": "manual-audit"})
            existing[rec.path] = row
            changed += 1
        elif rec.status == "gap":
            if rec.path in existing:
                del existing[rec.path]
                changed += 1

    out = sorted(existing.values(), key=lambda x: x["file"])
    assigns_path = repo_root / ".github" / "file-license-assignments.json"
    assigns_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    # Regenerate per-top-dir LICENSE_MANIFEST.md from the freshly-written ledger.
    try:
        regenerate_manifests(repo_root, out)
    except Exception:
        # Manifest regen must never break a successful save.
        pass

    return changed


def save_comments_flags(
    repo_root: Path,
    records: list[FileRecord],
    raw_flags_comments: dict[str, dict] | None = None,
) -> int:
    """Persist custom flag defs + per-file flags/comments into
    ``license_comments_flags.json``.

    File shape: ``{"flag_defs": [{id,label,color}...], "files": [...]}``.
    The files list is rebuilt from (a) the in-memory record set, where any
    record carrying flags or comments contributes an entry, and (b) entries
    from ``raw_flags_comments`` for files NOT present in ``records`` (so
    unrelated entries survive partial loads). Returns the number of file
    entries written.
    """
    out_map: dict[str, dict] = {}
    paths_in_memory: set[str] = set()
    for rec in records:
        paths_in_memory.add(rec.path)
        if rec.flags or rec.comments:
            out_map[rec.path] = {
                "file": rec.path,
                "flags": normalize_flags(rec.flags),
                "comments": list(rec.comments) if rec.comments else [],
            }
        # Clear flags_comments_dirty regardless - we just wrote authoritative state.
        rec.flags_comments_dirty = False
    if raw_flags_comments:
        for fp, entry in raw_flags_comments.items():
            if fp in paths_in_memory or not isinstance(entry, dict):
                continue
            flags = entry.get("flags")
            if flags is None:  # legacy single-flag entry
                flags = [entry["flag"]] if entry.get("flag") else []
            comments = entry.get("comments", []) or []
            if flags or comments:
                out_map[fp] = {
                    "file": fp,
                    "flags": [f for f in flags if f],
                    "comments": list(comments),
                }
    fc_path = repo_root / COMMENTS_FLAGS_FILENAME
    if not out_map and not CUSTOM_FLAG_DEFS:
        # Remove the file entirely when there's nothing to record.
        try:
            if fc_path.exists():
                fc_path.unlink()
        except Exception:
            pass
        return 0
    payload = {
        "flag_defs": [
            {"id": fv, "label": fl, "color": fc} for fv, fl, fc in CUSTOM_FLAG_DEFS
        ],
        "files": sorted(out_map.values(), key=lambda x: x["file"]),
    }
    fc_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return len(payload["files"])


def apply_license_to_author_awaiting(
    records: list[FileRecord],
    author: str,
    license_id: str,
) -> list[FileRecord]:
    """Promote every awaiting_license record by *author* to ``confirmed`` with
    the given license. Returns the list of records that were mutated. Caller
    must take an undo snapshot BEFORE calling this and refresh views after.
    """
    if not author or not license_id:
        return []
    mutated: list[FileRecord] = []
    for r in records:
        if r.status != "awaiting_license":
            continue
        if (r.contributor or "").lower() != author.lower():
            continue
        r.license = license_id
        r.status = "confirmed"
        r.author_confirmed = True
        r.dirty = True
        mutated.append(r)
    return mutated


def art_top_for_path(rel_path: str) -> str:
    """Return the ART_TOP_DIRS entry that *rel_path* belongs to, or "".

    Uses longest-prefix matching so nested entries (e.g. ``luaui/images``) win
    over a hypothetical shorter parent. ``rel_path`` is repo-relative with
    forward slashes.
    """
    norm = rel_path.replace("\\", "/")
    best = ""
    for top in ART_TOP_DIRS:
        if (norm == top or norm.startswith(top + "/")) and len(top) > len(best):
            best = top
    return best


def regenerate_manifests(repo_root: Path, raw_assigns: list[dict]) -> list[Path]:
    """(Re)write ``<top>/LICENSE_MANIFEST.md`` for every covered art directory.

    Mirrors the format produced by the ``confirm-on-merge`` workflow. For each
    directory in :data:`ART_TOP_DIRS` that has at least one recorded asset, a
    manifest file is written listing every asset (recursive) under that
    directory with license, contributor, PR and date.

    Returns the list of manifest paths written.
    """
    by_top: dict[str, list[dict]] = defaultdict(list)
    for entry in raw_assigns:
        fpath = entry.get("file", "")
        if not fpath:
            continue
        top = art_top_for_path(fpath)
        if top:
            by_top[top].append(entry)

    written: list[Path] = []
    for top in ART_TOP_DIRS:
        top_dir = repo_root / top
        if not top_dir.exists():
            continue
        entries = by_top.get(top, [])
        if not entries:
            # Don't pollute art directories with empty manifests.
            continue
        manifest_path = top_dir / "LICENSE_MANIFEST.md"

        # Relative hop back to repo root depends on how deep this top dir is
        # nested (e.g. "luaui/images" → "../../"). Keeps the legal/ link valid.
        legal_prefix = "../" * (top.count("/") + 1)

        # Sort by file path
        entries_sorted = sorted(entries, key=lambda x: x.get("file", ""))

        lines: list[str] = []
        lines.append(f"# License Manifest — `{top}/`")
        lines.append("")
        lines.append(
            "<!-- AUTO-GENERATED by the BAR License Auditor / "
            ".github/workflows/cla-check.yml on every PR merge. "
            "Do not edit by hand. -->"
        )
        lines.append("")
        lines.append(
            f"This file lists the recorded license, contributor, and source PR "
            f"for every art asset under `{top}/` that has passed through the BAR "
            f"CLA bot. Assets not yet processed through the CLA flow are not "
            f"listed here — they fall under the project policy in "
            f"[legal/POLICY_PROJECT_LICENSE.md]({legal_prefix}legal/POLICY_PROJECT_LICENSE.md)."
        )
        lines.append("")
        lines.append(f"Total assets recorded: **{len(entries_sorted)}**")
        lines.append("")
        lines.append("| File | License | Contributor | PR | Date |")
        lines.append("|---|---|---|---|---|")
        for e in entries_sorted:
            fpath = e.get("file", "").replace("\\", "/")
            # Strip the top-dir prefix so the file column is relative to top/
            sub = fpath[len(top) + 1:] if fpath.startswith(top + "/") else fpath
            lic = e.get("license", "") or ""
            contrib = e.get("contributor", "") or ""
            if e.get("submitted_by"):
                contrib += f" (submitted by {e['submitted_by']})"
            pr_raw = e.get("pr", "")
            pr_str = ""
            if pr_raw not in (None, "", 0):
                pr_str = str(pr_raw)
                if pr_str.isdigit():
                    pr_str = f"#{pr_str}"
            date_raw = e.get("date", "") or ""
            # Truncate ISO timestamp to YYYY-MM-DD
            date_str = date_raw.split("T", 1)[0] if date_raw else ""

            lic_cell = f"`{lic}`" if lic else ""
            if not lic and (e.get("status") == "awaiting_license"):
                lic_cell = "_awaiting license_"
            file_cell = f"`{sub}`" if sub else ""
            lines.append(
                f"| {file_cell} | {lic_cell} | {contrib} | {pr_str} | {date_str} |"
            )
        if not entries_sorted:
            lines.append("| _(no assets recorded yet)_ |  |  |  |  |")
        lines.append("")  # trailing newline

        manifest_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        written.append(manifest_path)

    return written
