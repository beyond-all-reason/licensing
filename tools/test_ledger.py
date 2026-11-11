# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for tools/ledger.py: lint rules, move, apply-consent.

Run:  python tools/test_ledger.py
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ledger  # noqa: E402

CONFIG = json.loads((HERE.parent / ".github" / "licensing.json").read_text(encoding="utf-8-sig"))


def _repo(tmp: Path, name: str, distribution: str) -> Path:
    r = tmp / name
    (r / ".github").mkdir(parents=True)
    cfg = dict(CONFIG)
    cfg["distribution"] = distribution
    (r / ".github" / "licensing.json").write_text(json.dumps(cfg), encoding="utf-8")
    (r / ".github" / "cla-signatures.json").write_text("[]\n", encoding="utf-8")
    return r


def _asset(r: Path, rel: str, data: bytes = b"RIFF") -> None:
    p = r / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _run(*argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ledger.main(list(argv))
    return rc, buf.getvalue()


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ledger-test-"))
        self.src = _repo(self.tmp, "public", "public")
        self.dst = _repo(self.tmp, "assets", "release-only")
        _asset(self.src, "sounds/weapons/a.wav")
        _asset(self.src, "sounds/weapons/b.wav")
        _asset(self.src, "objects3d/rock.s3o")
        _asset(self.src, "unitpics/x.png")
        rows = [
            {"file": "objects3d/rock.s3o", "license": "", "contributor": "Beherith", "pr": "manual-audit",
             "date": "2026-01-01T00:00:00Z", "source": "manual-audit", "status": "awaiting_license"},
            {"file": "sounds/weapons/a.wav", "license": "", "contributor": "icexuick", "pr": "manual-audit",
             "date": "2026-01-01T00:00:00Z", "source": "manual-audit", "status": "awaiting_license"},
            {"file": "sounds/weapons/b.wav", "license": "LicenseRef-BAR-CLA-1.0", "contributor": "icexuick",
             "pr": 12, "date": "2026-01-01T00:00:00Z", "source": "cla-bot"},
            {"file": "unitpics/x.png", "license": "", "contributor": "Mr Bob", "pr": "manual-audit",
             "date": "2026-01-01T00:00:00Z", "source": "manual-audit", "status": "awaiting_license"},
        ]
        ledger._write_json(self.src / ".github" / "file-license-assignments.json", rows)
        flags = {"flag_defs": [{"id": "sketchfab", "label": "SketchFab", "color": "#000"}],
                 "files": [
                     {"file": "objects3d/rock.s3o", "flags": ["sketchfab", "unsure"], "comments": [{"text": "pack 3"}]},
                     {"file": "sounds/weapons/a.wav", "flags": ["mixed-assets-multiple-asset-stores"], "comments": []},
                 ]}
        ledger._write_json(self.src / ledger.D.COMMENTS_FLAGS_FILENAME, flags)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ledger(self, r: Path) -> list[dict]:
        return json.loads((r / ".github" / "file-license-assignments.json").read_text(encoding="utf-8"))

    def test_lint_public_warns_about_store_flags(self):
        rc, out = _run("lint", str(self.src))
        self.assertEqual(rc, 0, out)
        self.assertIn("2 row(s) carry a store flag", out)

    def test_lint_warns_when_a_row_credits_a_mapped_login(self):
        (self.src / ".github" / "contributors.json").write_text(
            json.dumps({"_doc": "x", "Beherith": {"github": "Beherith"}, "Ice": {"github": "icexuick"}}), encoding="utf-8")
        rc, out = _run("lint", str(self.src))
        self.assertEqual(rc, 0, out)
        self.assertIn("2 row(s) credit the login 'icexuick'; contributors.json names them 'Ice'", out)
        self.assertNotIn("'Beherith'; contributors.json", out)

    def test_lint_rejects_release_only_in_public(self):
        rows = self._ledger(self.src)
        rows[0]["license"] = "LicenseRef-Sketchfab-Standard"
        rows[0].pop("status")
        ledger._write_json(self.src / ".github" / "file-license-assignments.json", rows)
        rc, out = _run("lint", str(self.src))
        self.assertEqual(rc, 1)
        self.assertIn("release-only row in a public ledger", out)
        self.assertIn("without distribution=release-only", out)
        self.assertIn("purchased row without sources", out)

    def test_move_dry_run_writes_nothing(self):
        rc, out = _run("move", str(self.src), str(self.dst), "--flag", "sketchfab")
        self.assertEqual(rc, 0, out)
        self.assertIn("dry run", out)
        self.assertTrue((self.src / "objects3d/rock.s3o").exists())
        self.assertFalse((self.dst / "objects3d/rock.s3o").exists())

    def test_move_applies_store_license_and_moves_flags(self):
        rc, out = _run("move", str(self.src), str(self.dst), "--flag", "sketchfab",
                       "--flag", "mixed-assets-multiple-asset-stores", "--apply")
        self.assertEqual(rc, 0, out)
        d = {e["file"]: e for e in self._ledger(self.dst)}
        self.assertEqual(set(d), {"objects3d/rock.s3o", "sounds/weapons/a.wav"})
        rock = d["objects3d/rock.s3o"]
        self.assertEqual(rock["license"], "LicenseRef-Sketchfab-Standard")
        self.assertEqual(rock["basis"], "purchased")
        self.assertEqual(rock["distribution"], "release-only")
        self.assertEqual(rock["sources"], [{"store": "sketchfab"}])
        self.assertEqual(rock["contributor"], "Beherith")
        self.assertNotIn("status", rock)
        self.assertEqual(d["sounds/weapons/a.wav"]["license"], "LicenseRef-ThirdParty-Mixed")
        self.assertEqual({e["file"] for e in self._ledger(self.src)}, {"sounds/weapons/b.wav", "unitpics/x.png"})
        self.assertFalse((self.src / "objects3d/rock.s3o").exists())
        self.assertTrue((self.dst / "objects3d/rock.s3o").exists())
        dflags = json.loads((self.dst / ledger.D.COMMENTS_FLAGS_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(dflags["files"], [{"file": "objects3d/rock.s3o", "flags": ["unsure"], "comments": [{"text": "pack 3"}]}])
        sflags = json.loads((self.src / ledger.D.COMMENTS_FLAGS_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(sflags["files"], [])
        self.assertTrue((self.dst / "objects3d" / "LICENSE_MANIFEST.md").exists())
        rc, out = _run("lint", str(self.dst))
        self.assertEqual(rc, 0, out)
        self.assertIn("source without item id", out)
        rc, out = _run("lint", str(self.src))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("store flag", out)

    def test_move_needs_target_license_for_unflagged(self):
        lst = self.tmp / "list.txt"
        lst.write_text("unitpics/x.png\n", encoding="utf-8")
        rc, out = _run("move", str(self.src), str(self.dst), "--list", str(lst), "--apply")
        self.assertEqual(rc, 1)
        self.assertIn("no target license", out)
        rc, out = _run("move", str(self.src), str(self.dst), "--list", str(lst),
                       "--to-license", "LicenseRef-Pixabay", "--apply")
        self.assertEqual(rc, 0, out)
        d = {e["file"]: e for e in self._ledger(self.dst)}
        self.assertEqual(d["unitpics/x.png"]["sources"], [{"store": "pixabay"}])

    def test_apply_consent(self):
        rc, out = _run("apply-consent", str(self.src), "--contributor", "mr bob",
                       "--license", "CC-BY-SA-4.0", "--ref", "123", "--source", "consent-issue")
        self.assertEqual(rc, 0, out)
        self.assertIn("dry run", out)
        self.assertEqual(self._ledger(self.src)[3]["license"], "")
        rc, out = _run("apply-consent", str(self.src), "--contributor", "Mr Bob",
                       "--license", "CC-BY-SA-4.0", "--ref", "123", "--source", "consent-issue", "--apply")
        self.assertEqual(rc, 0, out)
        row = {e["file"]: e for e in self._ledger(self.src)}["unitpics/x.png"]
        self.assertEqual((row["license"], row["pr"], row["source"]), ("CC-BY-SA-4.0", "123", "consent-issue"))
        self.assertNotIn("status", row)
        self.assertTrue((self.src / "unitpics" / "LICENSE_MANIFEST.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
