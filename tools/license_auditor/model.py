"""Qt table model and filter proxy for the BAR License Auditor."""
from __future__ import annotations

import re

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PyQt6.QtGui import QBrush, QColor, QFont

from .data import FileRecord, FLAG_LABEL, FLAG_COLOR, flags_display

COLUMNS = ["Status", "File", "License", "Author", "Submitter", "Reference", "Date", "Last Changed", "Actions", "Flags"]

COL_STATUS = 0
COL_FILE = 1
COL_LICENSE = 2
COL_CONTRIBUTOR = 3
COL_SUBMITTER = 4
COL_PR = 5
COL_DATE = 6
COL_LAST_CHANGED = 7
COL_ACTIONS = 8
COL_FLAG = 9

STATUS_COLOR: dict[str, QColor] = {
    "gap":              QColor("#ed4245"),  # Discord red
    "intent":           QColor("#f0b232"),  # Discord gold
    "awaiting_license": QColor("#eb8c1c"),  # orange — author known, license pending
    "confirmed":        QColor("#3ba55c"),  # Discord green
    "merged":           QColor("#5865f2"),  # Discord blurple
}

STATUS_LABEL: dict[str, str] = {
    "gap":              "Gap",
    "intent":           "Intent",
    "awaiting_license": "Awaiting license",
    "confirmed":        "Confirmed",
    "merged":           "Merged",
}


class FileTableModel(QAbstractTableModel):
    """Flat table of FileRecord objects — virtual rendering, handles 13k+ rows."""

    def __init__(self, records: list[FileRecord], parent=None):
        super().__init__(parent)
        self._records: list[FileRecord] = records

    # ------------------------------------------------------------------
    # QAbstractTableModel interface
    # ------------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: B008
        return len(self._records)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: B008
        return len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole and orientation == Qt.Orientation.Horizontal:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        rec = self._records[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return self._cell(rec, col)

        if role == Qt.ItemDataRole.ToolTipRole:
            if col == COL_STATUS:
                return STATUS_LABEL.get(rec.status, rec.status)
            if col == COL_PR:
                if rec.pr_url:
                    return rec.pr_url
                if rec.commit_url:
                    return rec.commit_url
            if col == COL_SUBMITTER:
                git = rec.git_author
                shown = rec.proxy_submitter or git
                if git and shown != git:
                    return f"git author: {git}"
            if col == COL_CONTRIBUTOR and not rec.contributor and rec.unitdef_author:
                return f"unitdef author: {rec.unitdef_author}"
            if col == COL_FLAG:
                parts = []
                if rec.flags:
                    parts.append(flags_display(rec.flags))
                if rec.comments:
                    parts.append(f"{len(rec.comments)} comment(s)")
                return " · ".join(parts) if parts else None
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == COL_STATUS:
                return QBrush(STATUS_COLOR.get(rec.status, QColor("white")))
            if col == COL_FLAG and rec.flags:
                return QBrush(QColor(FLAG_COLOR.get(rec.flags[0], "#bbbbbb")))
            if col == COL_FLAG and not rec.flags and rec.comments:
                return QBrush(QColor("#7a9ab0"))  # muted blue for comment-only
            if (col == COL_CONTRIBUTOR and rec.contributor
                    and not rec.author_confirmed):
                return QBrush(QColor("#7a8499"))

        if role == Qt.ItemDataRole.FontRole:
            tentative = (col == COL_CONTRIBUTOR and rec.contributor
                         and not rec.author_confirmed)
            if tentative or rec.dirty:
                f = QFont()
                if tentative:
                    f.setItalic(True)
                if rec.dirty:
                    f.setBold(True)
                return f

        if role == Qt.ItemDataRole.UserRole:
            return rec

        return None

    def _cell(self, rec: FileRecord, col: int) -> str:
        return {
            COL_FILE:         rec.path,
            COL_STATUS:       "",  # painted as dot by StatusDotDelegate
            COL_LICENSE:      rec.license,
            COL_CONTRIBUTOR:  rec.contributor,
            COL_SUBMITTER:    rec.proxy_submitter or rec.git_author,
            COL_PR:           f"#{rec.pr}" if rec.pr and rec.pr.isdigit() else (rec.pr if rec.pr else (rec.commit_hash[:7] if rec.commit_hash else "")),
            COL_DATE:         rec.date,
            COL_LAST_CHANGED: rec.last_changed,
            COL_FLAG:         (flags_display(rec.flags) if rec.flags else ("Commented" if rec.comments else "")),
            COL_ACTIONS:      "",
        }.get(col, "")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def refresh(self, records: list[FileRecord]) -> None:
        self.beginResetModel()
        self._records = records
        self.endResetModel()

    def notify_record(self, rec: FileRecord) -> None:
        """Emit dataChanged for the row containing *rec* (O(1) lookup via identity)."""
        try:
            row = self._records.index(rec)
        except ValueError:
            return
        top_left = self.index(row, 0)
        bot_right = self.index(row, self.columnCount() - 1)
        self.dataChanged.emit(top_left, bot_right)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() in (COL_LICENSE, COL_CONTRIBUTOR,
                              COL_FILE, COL_SUBMITTER, COL_PR, COL_DATE, COL_LAST_CHANGED,
                              COL_FLAG):
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def record_at_source_row(self, row: int) -> FileRecord:
        return self._records[row]


class FilterProxyModel(QSortFilterProxyModel):
    """Four orthogonal filters: status, top_dir, submitters, free-text on path."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status_filter: set[str] = set()  # empty = all
        self._top_dir_filter = ""       # '' = all
        self._text_filter = ""          # '' = all; lowercased substring or regex match
        self._text_field = "submitter"  # which field to search
        self._submitter_filter: set[str] = set()  # empty = all
        self._date_filter: set[str] = set()       # empty = all; exact YYYY-MM-DD match
        self._license_filter: set[str] = set()    # empty = all; exact license string match ('' = unlicensed)
        self._flag_filter: set[str] = set()       # empty = all; '' = unflagged
        self._dirty_only = False                  # show only records with unsaved changes
        self._use_regex = False                   # treat text_filter as a regex
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    # ------------------------------------------------------------------
    # Filter setters
    # ------------------------------------------------------------------

    def set_status(self, statuses: set[str]) -> None:
        """Filter to rows whose status is in *statuses*. Empty set = show all."""
        self._status_filter = statuses
        self.invalidateFilter()

    def set_top_dir(self, top_dir: str) -> None:
        self._top_dir_filter = top_dir
        self.invalidateFilter()

    def set_text(self, text: str) -> None:
        self._text_filter = text.lower().strip()
        self.invalidateFilter()

    def set_text_field(self, field: str) -> None:
        """Which FileRecord attribute to apply the text filter against."""
        self._text_field = field or "path"
        self.invalidateFilter()

    def set_submitters(self, names: set[str]) -> None:
        """Filter to rows whose effective submitter is in *names*. Empty = show all."""
        self._submitter_filter = names
        self.invalidateFilter()

    def set_date_filter(self, dates: set[str]) -> None:
        """Filter to rows where the date/last_changed field is one of *dates* (YYYY-MM-DD). Empty = show all."""
        self._date_filter = dates
        self.invalidateFilter()

    def set_license_filter(self, licenses: set[str]) -> None:
        """Filter to rows whose license is in *licenses*. Use '' for unlicensed. Empty set = show all."""
        self._license_filter = licenses
        self.invalidateFilter()

    def set_flag_filter(self, flags: set[str]) -> None:
        """Filter to rows carrying any flag in *flags*. Use '' for unflagged. Empty set = show all."""
        self._flag_filter = flags
        self.invalidateFilter()

    def set_dirty_only(self, dirty_only: bool) -> None:
        """When True, only rows with unsaved edits are shown."""
        self._dirty_only = dirty_only
        self.invalidateFilter()

    def set_use_regex(self, use_regex: bool) -> None:
        """When True, the text filter is compiled as a regex (re.IGNORECASE)."""
        self._use_regex = use_regex
        self.invalidateFilter()

    # ------------------------------------------------------------------
    # QSortFilterProxyModel override
    # ------------------------------------------------------------------

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        rec: FileRecord = self.sourceModel()._records[source_row]

        if self._status_filter and rec.status not in self._status_filter:
            return False
        if self._dirty_only and not rec.dirty:
            return False
        if self._top_dir_filter:
            pref = self._top_dir_filter
            p = rec.path
            if not (p == pref or p.startswith(pref + "/")):
                return False
        if self._license_filter:
            if rec.license not in self._license_filter:
                return False
        if self._flag_filter:
            rec_flags = rec.flags or [""]
            if not any(f in self._flag_filter for f in rec_flags):
                return False
        if self._date_filter:
            field_val = {
                "date":         rec.date,
                "last_changed": rec.last_changed,
            }.get(self._text_field, "")
            if field_val not in self._date_filter:
                return False
        elif self._text_filter:
            field_val = {
                "path":         rec.path,
                "submitter":    rec.proxy_submitter or rec.git_author,
                "contributor":  rec.contributor,
                "license":      rec.license,
                "date":         rec.date,
                "last_changed": rec.last_changed,
                "pr":           rec.pr,
                "flag":         flags_display(rec.flags) + " " + " ".join(rec.flags or []),
                "comments":     " ".join((c.get("text", "") if isinstance(c, dict) else str(c)) for c in (rec.comments or [])),
            }.get(self._text_field, rec.path)
            if self._use_regex:
                try:
                    if not re.search(self._text_filter, (field_val or ""), re.IGNORECASE):
                        return False
                except re.error:
                    return False
            else:
                if self._text_filter not in (field_val or "").lower():
                    return False
        if self._submitter_filter:
            effective = rec.proxy_submitter or rec.git_author or ""
            if effective not in self._submitter_filter:
                return False
        return True

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def source_record(self, proxy_index: QModelIndex) -> FileRecord | None:
        src = self.mapToSource(proxy_index)
        if not src.isValid():
            return None
        return self.sourceModel()._records[src.row()]

    def source_records_for_proxy_rows(self, proxy_rows: list[int]) -> list[FileRecord]:
        model = self.sourceModel()
        result = []
        for row in proxy_rows:
            src = self.mapToSource(self.index(row, 0))
            if src.isValid():
                result.append(model._records[src.row()])
        return result
