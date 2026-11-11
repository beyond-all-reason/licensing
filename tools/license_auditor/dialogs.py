"""Dialogs for the BAR License Auditor."""
from __future__ import annotations

import csv
import os
import re
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor

from .data import (
    ART_TOP_DIRS, FileRecord, KNOWN_LICENSES, LICENSE_META,
    release_only_licenses, store_flags, default_contributor, owned_licenses,
    FLAG_DEFS, BUILTIN_FLAG_DEFS, CUSTOM_FLAG_DEFS, flags_display, normalize_flags,
    PUBLIC_DOMAIN_LICENSE, PUBLIC_DOMAIN_CONTRIBUTOR, contributor_for_license,
)


class MultiFlagCombo(QComboBox):
    """Drop-down with one checkable entry per flag - a record can carry many.

    The popup stays open while entries are toggled; the closed combo shows the
    joined labels. Call repopulate() after the flag registry changed.
    """

    sig_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)
        self._ready = True
        self.repopulate()

    def repopulate(self) -> None:
        from PyQt6.QtGui import QStandardItem, QStandardItemModel
        checked = self.checked_flags() if self.count() else []
        model = QStandardItemModel(self)
        for fv, fl, fc in FLAG_DEFS:
            if fv == "":
                continue
            it = QStandardItem(fl)
            it.setData(fv, Qt.ItemDataRole.UserRole)
            it.setForeground(QColor(fc))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if fv in checked else Qt.CheckState.Unchecked)
            model.appendRow(it)
        self.setModel(model)
        self.view().viewport().installEventFilter(self)
        self._sync_text()

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() == QEvent.Type.MouseButtonRelease:
            self.showPopup()
            return True
        if obj is self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            try:
                pos = event.position().toPoint()
            except AttributeError:
                pos = event.pos()
            idx = self.view().indexAt(pos)
            it = self.model().itemFromIndex(idx)
            if it is not None:
                it.setCheckState(
                    Qt.CheckState.Unchecked if it.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked)
                self._sync_text()
                self.sig_changed.emit()
            return True  # keep the popup open
        return super().eventFilter(obj, event)

    def checked_flags(self) -> list[str]:
        out = []
        m = self.model()
        for i in range(m.rowCount()):
            it = m.item(i)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def set_checked(self, flags) -> None:
        flags = set(flags or [])
        m = self.model()
        for i in range(m.rowCount()):
            it = m.item(i)
            if it is not None:
                it.setCheckState(Qt.CheckState.Checked if it.data(Qt.ItemDataRole.UserRole) in flags
                                 else Qt.CheckState.Unchecked)
        self._sync_text()

    def _sync_text(self) -> None:
        if not getattr(self, "_ready", False):
            return
        self.lineEdit().setText(flags_display(self.checked_flags()) or "(none)")


class BulkEditDialog(QDialog):
    """Edit license, contributor, and status for one or many selected files."""

    def __init__(self, records: list[FileRecord], parent=None, *,
                 git_author_hint: str = "", dir_license_hint: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {len(records)} file(s)")
        self.setMinimumWidth(520)
        self._records = records

        layout = QVBoxLayout(self)

        # ── Summary of affected files ──────────────────────────────────
        MAX_PREVIEW = 8
        if len(records) <= MAX_PREVIEW:
            lines = "\n".join(f"  • {r.path}" for r in records)
        else:
            lines = "\n".join(f"  • {r.path}" for r in records[:MAX_PREVIEW])
            lines += f"\n  … and {len(records) - MAX_PREVIEW} more"
        summary = QLabel(lines)
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary.setStyleSheet("font-family: monospace;")
        layout.addWidget(summary)

        layout.addSpacing(8)

        # ── Edit form ──────────────────────────────────────────────────
        form = QFormLayout()
        layout.addLayout(form)

        # License selector
        self._license_combo = QComboBox()
        self._license_combo.addItem("(keep existing)", "")
        for lic in KNOWN_LICENSES:
            self._license_combo.addItem(lic, lic)
        shared_licenses = {r.license for r in records}
        if len(shared_licenses) == 1:
            lic = next(iter(shared_licenses))
            idx = self._license_combo.findData(lic)
            if idx >= 0:
                self._license_combo.setCurrentIndex(idx)
        elif not any(r.license for r in records) and dir_license_hint:
            idx = self._license_combo.findData(dir_license_hint)
            if idx >= 0:
                self._license_combo.setCurrentIndex(idx)
        form.addRow("License:", self._license_combo)

        # Contributor field
        self._contributor_edit = QLineEdit()
        self._contributor_edit.setPlaceholderText("GitHub handle (without @)")
        shared_contributors = {r.contributor for r in records}
        if len(shared_contributors) == 1:
            self._contributor_edit.setText(next(iter(shared_contributors)))
        elif all(not r.contributor for r in records) and git_author_hint:
            self._contributor_edit.setText(git_author_hint)
        form.addRow("Contributor:", self._contributor_edit)
        # CC0 (public domain) has no rights holder: while it is selected the
        # contributor field is locked to the "public domain" placeholder.
        self._prev_contributor = ""
        self._license_combo.currentIndexChanged.connect(self._sync_contributor_for_license)
        self._sync_contributor_for_license()

        # Status selector
        self._status_combo = QComboBox()
        self._status_combo.addItem("(keep existing)", "")
        self._status_combo.addItem("confirmed  —  write to file-license-assignments.json", "confirmed")
        self._status_combo.addItem("gap  —  remove from ledger", "gap")
        form.addRow("Status:", self._status_combo)
        # Flags: pick a set + how to apply it. Flags live in
        # license_comments_flags.json, not the license ledger.
        self._flag_mode_combo = QComboBox()
        for label, mode in (("(keep existing)", "keep"), ("Add selected flags", "add"),
                            ("Remove selected flags", "remove"),
                            ("Replace with selected flags", "replace")):
            self._flag_mode_combo.addItem(label, mode)
        self._flag_multi = MultiFlagCombo()
        shared = {tuple(normalize_flags(r.flags)) for r in records}
        if len(shared) == 1:
            self._flag_multi.set_checked(list(next(iter(shared))))
        _flag_wrap = QHBoxLayout()
        _flag_wrap.addWidget(self._flag_mode_combo)
        _flag_wrap.addWidget(self._flag_multi, stretch=1)
        form.addRow("Flags:", _flag_wrap)

        # ── Buttons ────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _sync_contributor_for_license(self, *_args) -> None:
        is_pd = self._license_combo.currentData() == PUBLIC_DOMAIN_LICENSE
        cur = self._contributor_edit.text()
        if is_pd:
            if cur != PUBLIC_DOMAIN_CONTRIBUTOR:
                self._prev_contributor = cur
            self._contributor_edit.setText(PUBLIC_DOMAIN_CONTRIBUTOR)
        elif cur == PUBLIC_DOMAIN_CONTRIBUTOR:
            self._contributor_edit.setText(self._prev_contributor)
        self._contributor_edit.setReadOnly(is_pd)

    def _validate_and_accept(self):
        status_val = self._status_combo.currentData()
        license_val = self._license_combo.currentData()
        contributor_val = self._contributor_edit.text().strip().lstrip("@")

        # When setting to confirmed, require both license and contributor
        if status_val == "confirmed":
            if not license_val:
                QMessageBox.warning(self, "Validation", "Choose a license before setting status to confirmed.")
                return
            if not contributor_val:
                QMessageBox.warning(self, "Validation", "Enter a contributor GitHub handle before setting status to confirmed.")
                return

        # Collect values NOW while widgets are still fully alive, before accept()
        # hides the dialog.  apply() reads from these instead of from widgets.
        self._accepted_license = license_val
        self._accepted_contributor = contributor_val
        self._accepted_status = status_val
        self._accepted_flag_mode = self._flag_mode_combo.currentData()
        self._accepted_flags = self._flag_multi.checked_flags()
        self.accept()

    def apply(self) -> int:
        """Apply changes to records in-place. Returns number of records modified."""
        # Read from the values captured in _validate_and_accept, not from widgets.
        # Widget C++ objects may have been cleaned up after exec() returns.
        license_val = getattr(self, "_accepted_license", "")
        contributor_val = getattr(self, "_accepted_contributor", "")
        status_val = getattr(self, "_accepted_status", "")
        flag_mode = getattr(self, "_accepted_flag_mode", "keep")
        flag_sel = getattr(self, "_accepted_flags", [])

        count = 0
        for rec in self._records:
            changed = False

            if license_val and rec.license != license_val:
                rec.license = license_val
                changed = True
            # Explicit entry wins, else keep existing.  CC0 forces the
            # "public domain" placeholder; other licenses drop a stale one.
            new_contrib = contributor_val or rec.contributor
            if license_val:
                new_contrib = contributor_for_license(rec, license_val, new_contrib)
            if new_contrib != rec.contributor:
                rec.contributor = new_contrib
                changed = True
            if status_val and rec.status != status_val:
                rec.status = status_val
                if status_val == "gap":
                    rec.license = ""
                    rec.contributor = ""
                    rec.proxy_submitter = ""
                    rec.pr = ""
                    rec.date = ""
                changed = True

            if changed:
                rec.dirty = True
            # Flags are tracked separately from the license ledger.
            flag_changed = False
            if flag_mode and flag_mode != "keep":
                cur = list(rec.flags or [])
                if flag_mode == "add":
                    new_flags = normalize_flags(cur + flag_sel)
                elif flag_mode == "remove":
                    new_flags = [f for f in cur if f not in set(flag_sel)]
                else:  # replace
                    new_flags = normalize_flags(flag_sel)
                if new_flags != cur:
                    rec.flags = new_flags
                    rec.flags_comments_dirty = True
                    flag_changed = True
            if changed or flag_changed:
                count += 1

        return count


class FlagWizardDialog(QDialog):
    """Edit -> Flag Wizard: create/remove custom flags (e.g. Envato, Sketchfab).

    Built-in flags are shown for reference and cannot be changed here. The
    accepted defs replace CUSTOM_FLAG_DEFS in the live registry and persist to
    license_comments_flags.json on the next save.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Flag Wizard")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Custom flags - e.g. one per store assets were bought from\n"
            "(Envato, Sketchfab, TurboSquid...). Persisted with the flags file (Ctrl+S)."))
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Label", "ID", "Color"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)
        for fv, fl, fc in CUSTOM_FLAG_DEFS:
            self._append_row(fv, fl, fc)
        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add flag…")
        add_btn.clicked.connect(self._add_flag)
        rm_btn = QPushButton("Remove selected")
        rm_btn.setToolTip("Removes the definition; files keep the raw flag id until re-flagged")
        rm_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        _bi = QLabel("Built-in: " + ", ".join(fl for fv, fl, _c in BUILTIN_FLAG_DEFS if fv))
        _bi.setWordWrap(True)
        _bi.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(_bi)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _append_row(self, fv: str, fl: str, fc: str) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        it_label = QTableWidgetItem(fl)
        it_id = QTableWidgetItem(fv)
        it_id.setFlags(it_id.flags() & ~Qt.ItemFlag.ItemIsEditable)
        it_color = QTableWidgetItem(fc)
        it_color.setForeground(QColor(fc))
        self._table.setItem(r, 0, it_label)
        self._table.setItem(r, 1, it_id)
        self._table.setItem(r, 2, it_color)

    def _add_flag(self) -> None:
        import re as _re
        label, ok = QInputDialog.getText(self, "New flag", "Flag label (e.g. Envato):")
        label = (label or "").strip()
        if not ok or not label:
            return
        fid = _re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        existing = {v for v, _l, _c in BUILTIN_FLAG_DEFS} | {
            self._table.item(r, 1).text() for r in range(self._table.rowCount())}
        if not fid or fid in existing:
            QMessageBox.warning(self, "Flag Wizard", f"A flag with id '{fid or label}' already exists.")
            return
        color = QColorDialog.getColor(QColor("#c08840"), self, f"Color for '{label}'")
        self._append_row(fid, label, color.name() if color.isValid() else "#c08840")

    def _remove_selected(self) -> None:
        for r in sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True):
            self._table.removeRow(r)

    def result_defs(self) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for r in range(self._table.rowCount()):
            fv = self._table.item(r, 1).text().strip()
            fl = (self._table.item(r, 0).text() or fv).strip()
            fc = (self._table.item(r, 2).text() or "#888888").strip()
            if fv:
                out.append((fv, fl, fc))
        return out


class CommitDialog(QDialog):
    """Review dirty records, write JSON, then git commit + optional push."""

    def __init__(self, repo_root: Path, dirty_records: list[FileRecord], parent=None,
                 *, before_state: "list[dict] | None" = None,
                 fc_records: "list[FileRecord] | None" = None,
                 fc_before: "dict[str, dict] | None" = None):
        super().__init__(parent)
        self.setWindowTitle("Commit & Push")
        self.resize(660, 560)
        self._repo_root = repo_root
        self._dirty = dirty_records
        self._fc_records = fc_records or []
        self._fc_before = fc_before or {}
        self._success = False

        layout = QVBoxLayout(self)

        # ── Status breakdown by change type ───────────────────────────
        from collections import Counter
        status_counts = Counter(r.status for r in dirty_records)
        breakdown_parts = [f"{v} {k}" for k, v in sorted(status_counts.items())]
        # Also count how many are status-changes vs. field-only edits
        if before_state:
            status_changes = sum(
                1 for r, b in zip(dirty_records, before_state)
                if b.get("status", "gap") != r.status
            )
            field_only = len(dirty_records) - status_changes
            extra = f"  ({status_changes} status-change(s), {field_only} field-edit(s))"
        else:
            extra = ""
        n = len(dirty_records)
        n_fc = len(self._fc_records)
        fc_extra = f"  +  {n_fc} flag/comment edit(s)" if n_fc else ""
        breakdown_lbl = QLabel(
            f"<b>{n} record(s) will be written</b>  —  "
            + "  •  ".join(breakdown_parts) + extra + fc_extra
        )
        breakdown_lbl.setStyleSheet("padding: 4px 0; font-size: 9pt;")
        layout.addWidget(breakdown_lbl)

        # ── Change preview ─────────────────────────────────────────────
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setMaximumHeight(140)
        MAX_PREVIEW = 60
        lines = []
        for rec in dirty_records[:MAX_PREVIEW]:
            marker = f"[{rec.status:9}]"
            lic = rec.license or "(none)"
            who = rec.contributor or "(none)"
            lines.append(f"{marker}  {rec.path}  →  {lic}  @{who}")
        if n > MAX_PREVIEW:
            lines.append(f"… and {n - MAX_PREVIEW} more")
        preview.setPlainText("\n".join(lines))
        layout.addWidget(preview)

        # ── Flag/comment change preview ───────────────────────────────
        if self._fc_records:
            fc_lbl = QLabel(f"<b>Flag / comment changes ({n_fc}):</b>")
            fc_lbl.setStyleSheet("padding: 2px 0; font-size: 9pt;")
            layout.addWidget(fc_lbl)
            fc_preview = QTextEdit()
            fc_preview.setReadOnly(True)
            fc_preview.setMaximumHeight(120)
            fc_lines: list[str] = []
            for rec in self._fc_records[:MAX_PREVIEW]:
                before = self._fc_before.get(rec.path, {})
                _old = before.get("flags")
                if _old is None:  # legacy single-flag snapshot
                    _old = [before["flag"]] if before.get("flag") else []
                old_flag = flags_display(_old) or "(none)"
                new_flag = flags_display(rec.flags) or "(none)"
                old_comments = before.get("comments", []) or []
                new_comments = rec.comments or []
                parts: list[str] = []
                if old_flag != new_flag:
                    parts.append(f"flag: {old_flag} → {new_flag}")
                added = len(new_comments) - len(old_comments)
                if added > 0:
                    last = new_comments[-1] if new_comments else {}
                    snippet = (last.get("text") or "").splitlines()[0][:48]
                    parts.append(f"+{added} comment(s): \"{snippet}\"")
                elif added < 0:
                    parts.append(f"{-added} comment(s) removed")
                elif new_comments != old_comments:
                    parts.append("comments edited")
                if parts:
                    fc_lines.append(f"  {rec.path}  →  " + "; ".join(parts))
            if n_fc > MAX_PREVIEW:
                fc_lines.append(f"… and {n_fc - MAX_PREVIEW} more")
            fc_preview.setPlainText("\n".join(fc_lines) if fc_lines else "(no diff)")
            layout.addWidget(fc_preview)

        # ── JSON diff expander ─────────────────────────────────────────
        diff_label = "View JSON diff…" if before_state else "View JSON to be written…"
        self._json_toggle = QPushButton(f"▶  {diff_label}")
        self._json_toggle.setCheckable(True)
        self._json_toggle.setStyleSheet(
            "QPushButton { border: none; text-align: left; color: #7a9ab0;"
            " font-size: 8pt; padding: 2px 0; }"
            " QPushButton:checked { color: #a8c8e8; }"
        )
        self._json_toggle.toggled.connect(lambda checked: self._toggle_json_view(checked, diff_label))
        layout.addWidget(self._json_toggle)

        self._json_view = QTextEdit()
        self._json_view.setReadOnly(True)
        self._json_view.setFontFamily("Courier New")
        self._json_view.setMaximumHeight(160)
        self._json_view.setVisible(False)

        import json as _json
        if before_state:
            # Field-level before → after diff per record
            _FIELDS = [("status", "status"), ("license", "license"),
                       ("contributor", "contributor"), ("pr", "pr"), ("date", "date")]
            diff_lines: list[str] = []
            for rec, before in zip(dirty_records, before_state):
                changes: list[str] = []
                for field, key in _FIELDS:
                    old_val = before.get(key, "") or ""
                    new_val = getattr(rec, field, "") or ""
                    if old_val != new_val:
                        changes.append(
                            f"  {field:<13} {(old_val or '(none)')!s:24}  →  {new_val or '(none)'}"
                        )
                if changes:
                    diff_lines.append(f"--- {rec.path}")
                    diff_lines.extend(changes)
                    diff_lines.append("")
            self._json_view.setPlainText("\n".join(diff_lines) if diff_lines else "(no field changes detected)")
        else:
            json_records = [
                {
                    "path":        r.path,
                    "status":      r.status,
                    "license":     r.license,
                    "contributor": r.contributor,
                    **({"pr": r.pr} if r.pr else {}),
                    **({"date": r.date} if r.date else {}),
                }
                for r in dirty_records
            ]
            self._json_view.setPlainText(_json.dumps(json_records, indent=2))
        layout.addWidget(self._json_view)

        # ── Commit form ────────────────────────────────────────────────
        form = QFormLayout()
        self._msg_edit = QLineEdit()
        confirmed_count = sum(1 for r in dirty_records if r.status == "confirmed")
        awaiting_count = sum(1 for r in dirty_records if r.status == "awaiting_license")
        if confirmed_count:
            default_msg = f"CLA: Manual audit — assign licenses to {confirmed_count} file(s)"
        elif awaiting_count:
            default_msg = f"CLA: Manual audit — {awaiting_count} file(s) awaiting license"
        elif n_fc:
            default_msg = f"CLA: Manual audit — {n_fc} flag/comment update(s)"
        else:
            default_msg = "CLA: Manual audit"
        self._msg_edit.setText(default_msg)
        form.addRow("Commit message:", self._msg_edit)
        layout.addLayout(form)

        self._push_check = QCheckBox("Push to remote after commit")
        self._push_check.setChecked(True)
        layout.addWidget(self._push_check)

        # ── Git output ─────────────────────────────────────────────────
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFontFamily("Courier New")
        layout.addWidget(self._output)

        # ── Buttons ────────────────────────────────────────────────────
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Commit")
        self._buttons.accepted.connect(self._do_commit)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _toggle_json_view(self, checked: bool, label: str = "View JSON to be written…") -> None:
        self._json_toggle.setText(("▼" if checked else "▶") + f"  {label}")
        self._json_view.setVisible(checked)
        if checked:
            self.resize(self.width(), max(self.height(), 640))

    def _run(self, *args: str, env: dict | None = None) -> tuple[bool, str]:
        try:
            r = subprocess.run(
                list(args),
                cwd=str(self._repo_root),
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            out = (r.stdout + r.stderr).strip()
            return r.returncode == 0, out
        except Exception as e:
            return False, str(e)

    def _log(self, text: str) -> None:
        self._output.append(text)
        self._output.ensureCursorVisible()

    def _update_buttons_on_success(self) -> None:
        """Replace buttons with only Close button after successful commit."""
        # Remove old buttons from layout
        parent_layout = self._buttons.parent().layout()
        if parent_layout:
            parent_layout.removeWidget(self._buttons)
        
        # Create new buttons with only Close
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close
        )
        self._buttons.rejected.connect(self.accept)
        
        # Add back to layout
        parent_layout.addWidget(self._buttons)

    def _do_commit(self):
        msg = self._msg_edit.text().strip()
        if not msg:
            QMessageBox.warning(self, "Error", "Commit message is required.")
            return

        self._output.clear()
        self._buttons.setEnabled(False)

        # Build the `git add` argument list. Always include the two ledgers;
        # include each per-top-dir LICENSE_MANIFEST.md only if it actually
        # exists on disk (git add errors on missing paths).
        add_args = ["git", "add",
                    ".github/cla-signatures.json",
                    ".github/file-license-assignments.json"]
        for top in ART_TOP_DIRS:
            rel = f"{top}/LICENSE_MANIFEST.md"
            if (self._repo_root / rel).exists():
                add_args.append(rel)

        # Include the comments/flags file when present (it's removed by the
        # auditor when empty so don't error on a missing path).
        fc_rel = "license_comments_flags.json"
        if (self._repo_root / fc_rel).exists():
            add_args.append(fc_rel)

        bot_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "BAR CLA Bot",
            "GIT_COMMITTER_NAME": "BAR CLA Bot",
            "GIT_AUTHOR_EMAIL": "cla-bot@beyondallreason.info",
            "GIT_COMMITTER_EMAIL": "cla-bot@beyondallreason.info",
        }

        steps = [
            ("git add",    add_args, None),
            ("git commit", ["git", "commit", "-m", msg], bot_env),
        ]
        if self._push_check.isChecked():
            steps.append(("git push", ["git", "push"], None))

        for label, cmd, step_env in steps:
            ok, out = self._run(*cmd, env=step_env)
            self._log(f"$ {' '.join(cmd)}\n{out}\n")
            if not ok:
                if label == "git commit" and "nothing to commit" in out:
                    self._log("Nothing new to commit — JSON may not have been saved yet.")
                else:
                    self._log(f"❌  {label} failed — stopping.")
                self._buttons.setEnabled(True)
                return

        self._log("✅  Done.")
        self._success = True
        self._update_buttons_on_success()

    @property
    def success(self) -> bool:
        return self._success


# ---------------------------------------------------------------------------
# CSV Import dialog — Session 7
# ---------------------------------------------------------------------------

class _CsvImportDialog(QDialog):
    """Map CSV columns to FileRecord fields, preview match count, then apply.

    Usage::

        dlg = _CsvImportDialog(csv_path, all_records, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            count = dlg.apply()   # mutates records in-place, sets dirty=True
    """

    _IMPORTABLE: list[tuple[str, str]] = [
        ("license",     "License"),
        ("contributor", "Contributor"),
        ("status",      "Status"),
        ("pr",          "PR"),
        ("date",        "Date"),
    ]

    def __init__(self, csv_path: str, records: list[FileRecord], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import CSV — Column Mapping")
        self.setMinimumWidth(500)

        self._csv_path = csv_path
        self._path_to_rec: dict[str, FileRecord] = {r.path: r for r in records}
        self._csv_rows: list[dict] = []
        self._csv_headers: list[str] = []

        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                self._csv_headers = list(reader.fieldnames or [])
                self._csv_rows = list(reader)
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(parent, "CSV Error", str(exc))

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            f"<b>{Path(csv_path).name}</b> — {len(self._csv_rows)} rows, "
            f"{len(self._csv_headers)} columns"
        ))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Path column (required for matching)
        self._path_combo = QComboBox()
        for h in self._csv_headers:
            self._path_combo.addItem(h)
        for i, h in enumerate(self._csv_headers):
            if h.lower() in ("file", "path", "filepath", "file path"):
                self._path_combo.setCurrentIndex(i)
                break
        form.addRow("Path column (required):", self._path_combo)
        layout.addLayout(form)

        # Importable field mappers
        field_form = QFormLayout()
        field_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._field_combos: dict[str, QComboBox] = {}
        for field, label in self._IMPORTABLE:
            cb = QComboBox()
            cb.addItem("(ignore)")
            for h in self._csv_headers:
                cb.addItem(h)
            # Auto-detect by matching header name
            for i, h in enumerate(self._csv_headers):
                if h.lower() in (field.lower(), label.lower()):
                    cb.setCurrentIndex(i + 1)
                    break
            self._field_combos[field] = cb
            field_form.addRow(f"{label}:", cb)
        layout.addLayout(field_form)

        # Preview label
        self._preview = QLabel()
        self._preview.setStyleSheet("color: #99bbdd; padding: 4px 0;")
        layout.addWidget(self._preview)
        self._path_combo.currentIndexChanged.connect(self._refresh_preview)
        self._refresh_preview()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_preview(self) -> None:
        path_col = self._path_combo.currentText()
        matches = sum(
            1 for row in self._csv_rows
            if row.get(path_col, "") in self._path_to_rec
        )
        self._preview.setText(
            f"→ {matches} of {len(self._csv_rows)} rows match existing records (by path)"
        )

    def apply(self) -> int:
        """Mutate matching records in-place. Returns count of modified records."""
        path_col = self._path_combo.currentText()
        field_map = {
            field: cb.currentText()
            for field, cb in self._field_combos.items()
            if cb.currentIndex() > 0
        }
        count = 0
        for row in self._csv_rows:
            path = row.get(path_col, "").strip()
            rec = self._path_to_rec.get(path)
            if rec is None:
                continue
            changed = False
            for field, col in field_map.items():
                val = row.get(col, "").strip()
                if val and getattr(rec, field, "") != val:
                    setattr(rec, field, val)
                    changed = True
            if changed:
                rec.dirty = True
                count += 1
        return count


class PurchasedDialog(QDialog):
    """Record the selected files as store purchases: store license, release-only."""

    def __init__(self, records: list[FileRecord], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Mark {len(records)} file(s) as purchased")
        self.setMinimumWidth(560)
        self._records = records
        layout = QVBoxLayout(self)
        max_preview = 8
        lines = "\n".join(f"  \u2022 {r.path}" for r in records[:max_preview])
        if len(records) > max_preview:
            lines += f"\n  \u2026 and {len(records) - max_preview} more"
        summary = QLabel(lines)
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary.setStyleSheet("font-family: monospace;")
        layout.addWidget(summary)
        note = QLabel("Sets the store license, basis = purchased and distribution = release-only. "
                      "The contributor stays the person who integrated the file. Rows under a store "
                      "license must leave a public repository with `ledger move`.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)
        self._license_combo = QComboBox()
        for lic in release_only_licenses():
            self._license_combo.addItem(f"{lic}  \u2014  {LICENSE_META.get(lic, {}).get('name', '')}", lic)
        form.addRow("Store license:", self._license_combo)
        self._item = QLineEdit()
        self._item.setPlaceholderText("store item id or product slug")
        self._code = QLineEdit()
        self._code.setPlaceholderText("licence or certificate code (optional)")
        self._licensee = QLineEdit()
        self._licensee.setPlaceholderText("account the item was bought under")
        self._date = QLineEdit()
        self._date.setPlaceholderText("YYYY-MM-DD")
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://\u2026 (optional)")
        form.addRow("Item:", self._item)
        form.addRow("Licence code:", self._code)
        form.addRow("Licensee:", self._licensee)
        form.addRow("Purchase date:", self._date)
        form.addRow("URL:", self._url)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self):
        lic = self._license_combo.currentData()
        if not lic:
            QMessageBox.warning(self, "Validation", "licensing.json defines no release-only (store) license.")
            return
        date = self._date.text().strip()
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            QMessageBox.warning(self, "Validation", "Purchase date must be YYYY-MM-DD.")
            return
        missing = [r.path for r in self._records if not (r.contributor or default_contributor(r))]
        if missing:
            QMessageBox.warning(self, "Validation",
                                f"{len(missing)} file(s) have no contributor; set the integrator first.")
            return
        self._accepted = {
            "license": lic,
            "item": self._item.text().strip(),
            "licence_code": self._code.text().strip(),
            "licensee": self._licensee.text().strip(),
            "date": date,
            "url": self._url.text().strip(),
        }
        self.accept()

    def apply(self) -> int:
        """Apply to the records in place; returns the number changed."""
        a = getattr(self, "_accepted", None)
        if not a:
            return 0
        lic = a["license"]
        src = {"store": LICENSE_META.get(lic, {}).get("store", "")}
        for k in ("item", "licence_code", "licensee", "date", "url"):
            if a.get(k):
                src[k] = a[k]
        store_ids = store_flags()
        count = 0
        for rec in self._records:
            rec.license = lic
            rec.status = "confirmed"
            rec.contributor = rec.contributor or default_contributor(rec)
            rec.author_confirmed = bool(rec.contributor)
            extra = dict(rec.extra or {})
            extra["basis"] = "purchased"
            extra["distribution"] = "release-only"
            sources = [s for s in extra.get("sources", []) if isinstance(s, dict) and s.get("item")]
            if src.get("item"):
                if not any(s.get("store") == src["store"] and s.get("item") == src["item"] for s in sources):
                    sources.append(src)
            elif not sources:
                sources = [src]
            extra["sources"] = sources
            rec.extra = extra
            rec.dirty = True
            kept = [f for f in (rec.flags or []) if f not in store_ids]
            if kept != list(rec.flags or []):
                rec.flags = kept
                rec.flags_comments_dirty = True
            count += 1
        return count


class OwnedDialog(QDialog):
    """Record the selected files as owned work: a licence that names a rights holder."""

    def __init__(self, records: list[FileRecord], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Mark {len(records)} file(s) as owned work")
        self.setMinimumWidth(560)
        self._records = records
        layout = QVBoxLayout(self)
        max_preview = 8
        lines = "\n".join(f"  \u2022 {r.path}" for r in records[:max_preview])
        if len(records) > max_preview:
            lines += f"\n  \u2026 and {len(records) - max_preview} more"
        summary = QLabel(lines)
        summary.setStyleSheet("font-family: monospace;")
        layout.addWidget(summary)
        note = QLabel("For commissioned or in-house work whose rights were assigned to the company. "
                      "The contributor stays the credited author; the rights holder is recorded separately.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)
        self._license_combo = QComboBox()
        for lic in owned_licenses():
            self._license_combo.addItem(f"{lic}  \u2014  {LICENSE_META.get(lic, {}).get('name', '')}", lic)
        form.addRow("License:", self._license_combo)
        self._holder = QLineEdit()
        self._basis = QComboBox()
        for label, val in (("assignment (contract or deed)", "assignment"), ("in-house", "in-house")):
            self._basis.addItem(label, val)
        form.addRow("Rights holder:", self._holder)
        form.addRow("Basis:", self._basis)
        self._license_combo.currentIndexChanged.connect(self._default_holder)
        self._default_holder()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _default_holder(self, *_args) -> None:
        lic = self._license_combo.currentData() or ""
        if not self._holder.text().strip():
            self._holder.setText(LICENSE_META.get(lic, {}).get("rights_holder_default", ""))

    def _validate_and_accept(self):
        lic = self._license_combo.currentData()
        if not lic:
            QMessageBox.warning(self, "Validation", "licensing.json defines no license that names a rights holder.")
            return
        if not self._holder.text().strip():
            QMessageBox.warning(self, "Validation", "Enter the rights holder.")
            return
        missing = [r.path for r in self._records if not (r.contributor or default_contributor(r))]
        if missing:
            QMessageBox.warning(self, "Validation",
                                f"{len(missing)} file(s) have no contributor; set the credited author first.")
            return
        self._accepted = {"license": lic, "rights_holder": self._holder.text().strip(),
                          "basis": self._basis.currentData()}
        self.accept()

    def apply(self) -> int:
        a = getattr(self, "_accepted", None)
        if not a:
            return 0
        for rec in self._records:
            rec.license = a["license"]
            rec.status = "confirmed"
            rec.contributor = rec.contributor or default_contributor(rec)
            rec.author_confirmed = bool(rec.contributor)
            extra = dict(rec.extra or {})
            extra["rights_holder"] = a["rights_holder"]
            extra["basis"] = a["basis"]
            rec.extra = extra
            rec.dirty = True
        return len(self._records)
