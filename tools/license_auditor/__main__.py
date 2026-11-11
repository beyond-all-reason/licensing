"""Entry point — run as: python -m license_auditor [/path/to/bar/repo]"""
from __future__ import annotations

import sys
import os
import traceback
from datetime import datetime
from pathlib import Path

# Self-heal: if this file is being run as a script (no parent package),
# re-exec ourselves as `python -m license_auditor` so relative imports work.
if __package__ in (None, ""):
    _pkg_dir = Path(__file__).resolve().parent
    _parent = _pkg_dir.parent  # tools/
    os.chdir(_parent)
    os.execv(sys.executable, [sys.executable, "-m", _pkg_dir.name] + sys.argv[1:])

from PyQt6.QtWidgets import QApplication, QMessageBox

from .app import MainWindow

# Log file sits next to this package so it survives app restarts
_LOG_PATH = Path(__file__).parent / "auditor.log"


def _write_log(text: str) -> None:
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def _excepthook(exc_type, exc_value, exc_tb):
    """Show unhandled exceptions in a dialog and write them to auditor.log."""
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(msg, file=sys.stderr)
    _write_log(f"\n{'='*60}\n[{datetime.now():%Y-%m-%d %H:%M:%S}] UNHANDLED EXCEPTION\n{msg}")
    try:
        dlg = QMessageBox()
        dlg.setWindowTitle("Unhandled error")
        dlg.setText(str(exc_value))
        dlg.setDetailedText(msg)
        dlg.setIcon(QMessageBox.Icon.Critical)
        dlg.exec()
    except Exception:
        pass


class _TeeStream:
    """Duplicate writes to both the original stream and the log file."""
    def __init__(self, original):
        self._orig = original

    def write(self, text: str) -> int:
        _write_log(text)
        return self._orig.write(text)

    def flush(self):
        self._orig.flush()

    def __getattr__(self, name):
        return getattr(self._orig, name)


def main() -> None:
    # Write a session header so the log clearly separates runs
    _write_log(f"\n{'='*60}\n[{datetime.now():%Y-%m-%d %H:%M:%S}] SESSION START\n")
    # Also write to reload_debug.log so we can confirm which build is running
    try:
        with (Path(__file__).parent / "reload_debug.log").open("a", encoding="utf-8") as _f:
            _f.write(f"\n[{datetime.now():%H:%M:%S}] SESSION START pid={os.getpid()} argv={sys.argv}\n")
    except Exception:
        pass

    # Tee stderr → log file so Qt warnings and tracebacks are captured
    sys.stderr = _TeeStream(sys.stderr)

    app = QApplication(sys.argv)
    app.setApplicationName("BAR License Auditor")

    sys.excepthook = _excepthook

    repo_root: Path | None = None
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if not p.is_dir():
            print(f"Error: '{p}' is not a directory.", file=sys.stderr)
            sys.exit(1)
        repo_root = p

    win = MainWindow(repo_root)
    win.show()
    code = app.exec()
    _write_log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] SESSION END (exit {code})\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
