# SPDX-License-Identifier: GPL-2.0-or-later
"""Live-reload wrapper for the BAR License Auditor.

Usage:
    python tools/live_reload.py [repo_root]

Watches tools/license_auditor/*.py for changes and restarts the app
automatically.  Press Ctrl+C to quit.
"""
from __future__ import annotations

import sys
import os
import time
import subprocess
import hashlib
from pathlib import Path


WATCH_DIR = Path(__file__).parent / "license_auditor"
POLL_INTERVAL = 0.8   # seconds


def _snapshot(watch_dir: Path) -> dict[Path, bytes]:
    snap = {}
    for p in watch_dir.rglob("*.py"):
        try:
            snap[p] = hashlib.md5(p.read_bytes()).digest()
        except OSError:
            pass
    return snap


def _changed(old: dict, new: dict) -> list[str]:
    changed = []
    for p, h in new.items():
        if old.get(p) != h:
            changed.append(p.name)
    for p in old:
        if p not in new:
            changed.append(p.name + " (deleted)")
    return changed


def main():
    repo_args = sys.argv[1:]   # pass through to auditor

    cmd = [sys.executable, "-m", "tools.license_auditor"] + repo_args
    cwd = Path(__file__).parent.parent   # repo root

    snap = _snapshot(WATCH_DIR)
    proc: subprocess.Popen | None = None

    def start() -> subprocess.Popen:
        print(f"\033[32m[live-reload] Starting…\033[0m", flush=True)
        return subprocess.Popen(cmd, cwd=cwd)

    try:
        proc = start()
        while True:
            time.sleep(POLL_INTERVAL)

            new_snap = _snapshot(WATCH_DIR)
            hits = _changed(snap, new_snap)
            if hits:
                snap = new_snap
                print(
                    f"\033[33m[live-reload] Changed: {', '.join(hits)} — restarting…\033[0m",
                    flush=True,
                )
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                proc = start()

            # If the app closed by itself, restart only if files changed next poll
            # (avoids restart-loop when the user closes the window intentionally)
            if proc.poll() is not None:
                # Wait one more poll to see if files changed
                time.sleep(POLL_INTERVAL)
                new_snap2 = _snapshot(WATCH_DIR)
                hits2 = _changed(snap, new_snap2)
                if hits2:
                    snap = new_snap2
                    print(
                        f"\033[33m[live-reload] Changed: {', '.join(hits2)} — restarting…\033[0m",
                        flush=True,
                    )
                    proc = start()
                else:
                    print("\033[90m[live-reload] App closed. Waiting for file changes…\033[0m", flush=True)
                    # Block until a change occurs
                    while True:
                        time.sleep(POLL_INTERVAL)
                        new_snap3 = _snapshot(WATCH_DIR)
                        hits3 = _changed(snap, new_snap3)
                        if hits3:
                            snap = new_snap3
                            print(
                                f"\033[33m[live-reload] Changed: {', '.join(hits3)} — restarting…\033[0m",
                                flush=True,
                            )
                            proc = start()
                            break

    except KeyboardInterrupt:
        print("\n\033[31m[live-reload] Stopped.\033[0m", flush=True)
        if proc and proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
