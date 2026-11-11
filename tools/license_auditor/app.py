"""Main window for the BAR License Auditor."""

from __future__ import annotations



import csv

import sys

import subprocess

import json

from datetime import datetime, timezone

from pathlib import Path



from PyQt6.QtWidgets import (

    QAbstractItemView,

    QApplication,

    QCalendarWidget,

    QComboBox,

    QDialog,

    QDialogButtonBox,

    QFileDialog,

    QHeaderView,

    QLabel,

    QLineEdit,

    QListWidget,

    QListWidgetItem,

    QMainWindow,

    QMenu,

    QMessageBox,

    QProgressBar,

    QPushButton,

    QSplitter,

    QStackedWidget,

    QStatusBar,

    QStyle,

    QStyledItemDelegate,

    QStyleOptionViewItem,

    QTabWidget,

    QToolBar,

    QTableView,

    QTextEdit,

    QTreeWidget,

    QTreeWidgetItem,

    QWhatsThis,

    QWidget,

    QHBoxLayout,

    QVBoxLayout,

)

from PyQt6.QtCore import Qt, QDate, QEvent, QItemSelectionModel, QRect, QRectF, QSettings, QSize, QThread, QTimer, QUrl, pyqtSignal

from PyQt6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QKeySequence, QPainter, QPalette, QPen, QPixmap, QShortcut, QTextCharFormat, QUndoCommand, QUndoStack



import copy
from .data import ART_TOP_DIRS, FileRecord, KNOWN_LICENSES, FLAG_DEFS, FLAG_LABEL, FLAG_COLOR, FLAG_VALUES, COMMENTS_FLAGS_FILENAME, load_repo, fetch_git_info, save_assignments, save_comments_flags, apply_license_to_author_awaiting, is_public_domain_contributor, default_contributor, contributor_for_license, unit_key_candidates, set_custom_flag_defs, CUSTOM_FLAG_DEFS, normalize_flags, flags_display

from .model import COL_ACTIONS, COL_CONTRIBUTOR, COL_DATE, COL_FILE, COL_FLAG, COL_LAST_CHANGED, COL_LICENSE, COL_PR, COL_STATUS, COL_SUBMITTER, FileTableModel, FilterProxyModel, STATUS_COLOR, STATUS_LABEL

from .dialogs import BulkEditDialog, CommitDialog, PurchasedDialog, OwnedDialog, FlagWizardDialog, MultiFlagCombo, _CsvImportDialog





# ---------------------------------------------------------------------------

# Multi-date calendar picker (popup dialog + compact toolbar widget)

# ---------------------------------------------------------------------------



class _MultiDateCalendarDialog(QDialog):

    """Modal popup with a QCalendarWidget that supports drag-to-select multiple dates."""



    def __init__(self, selected_dates: set, parent=None):

        super().__init__(parent)

        self.setWindowTitle("Select dates to filter")

        self._selected: set[QDate] = set(selected_dates)

        self._prev_highlighted: set[QDate] = set()

        self._drag_active = False

        self._drag_start: QDate | None = None

        self._drag_adding = True

        self._pre_drag_selected: set[QDate] = set()



        layout = QVBoxLayout(self)



        self._cal = QCalendarWidget()

        self._cal.setGridVisible(True)

        self._cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)

        layout.addWidget(self._cal)



        self._label = QLabel("No dates selected — click or drag to select days")

        layout.addWidget(self._label)



        btn_row = QHBoxLayout()

        btn_clear = QPushButton("Clear all")

        btn_clear.clicked.connect(self._clear_all)

        btn_done = QPushButton("Done")

        btn_done.setDefault(True)

        btn_done.clicked.connect(self.accept)

        btn_row.addWidget(btn_clear)

        btn_row.addStretch()

        btn_row.addWidget(btn_done)

        layout.addLayout(btn_row)



        # Install drag-select event filter on the calendar's internal table view

        cal_view = self._cal.findChild(QAbstractItemView)

        if cal_view is not None:

            cal_view.viewport().setMouseTracking(True)

            cal_view.viewport().installEventFilter(self)



        self._refresh_highlights()

        self._refresh_label()



    # ------------------------------------------------------------------

    # Drag-select

    # ------------------------------------------------------------------



    def _date_at_viewport_pos(self, pos) -> "QDate | None":

        """Return the QDate for the calendar cell under *pos* (viewport coords)."""

        tv = self._cal.findChild(QAbstractItemView)

        if tv is None:

            return None

        idx = tv.indexAt(pos)

        if not idx.isValid():

            return None

        try:

            int(idx.data(Qt.ItemDataRole.DisplayRole))

        except (TypeError, ValueError):

            return None

        year = self._cal.yearShown()

        month = self._cal.monthShown()

        first_day = QDate(year, month, 1)

        # dayOfWeek() → int 1=Mon..7=Sun; firstDayOfWeek().value → same range

        offset = (first_day.dayOfWeek() - self._cal.firstDayOfWeek().value) % 7

        day_offset = idx.row() * 7 + idx.column() - offset + 1

        return first_day.addDays(day_offset - 1)



    def _apply_drag_range(self, end_date: QDate) -> None:

        """Select/deselect every date from _drag_start to end_date inclusive."""

        self._selected = set(self._pre_drag_selected)

        a, b = sorted([self._drag_start, end_date], key=lambda d: d.toJulianDay())

        d = a

        while d <= b:

            if self._drag_adding:

                self._selected.add(d)

            else:

                self._selected.discard(d)

            d = d.addDays(1)

        self._refresh_highlights()

        self._refresh_label()



    def eventFilter(self, obj, event) -> bool:

        etype = event.type()

        if etype == QEvent.Type.MouseButtonPress:

            if event.button() == Qt.MouseButton.LeftButton:

                try:

                    pos = event.position().toPoint()

                except AttributeError:

                    pos = event.pos()

                date = self._date_at_viewport_pos(pos)

                if date is not None and date.isValid():

                    self._drag_active = True

                    self._drag_start = date

                    self._drag_adding = date not in self._selected

                    self._pre_drag_selected = set(self._selected)

                    self._apply_drag_range(date)

                    return True

        elif etype == QEvent.Type.MouseMove:

            if self._drag_active:

                try:

                    pos = event.position().toPoint()

                except AttributeError:

                    pos = event.pos()

                date = self._date_at_viewport_pos(pos)

                if date is not None and date.isValid():

                    self._apply_drag_range(date)

                return True

        elif etype == QEvent.Type.MouseButtonRelease:

            if self._drag_active and event.button() == Qt.MouseButton.LeftButton:

                self._drag_active = False

                return True

        return False



    def _clear_all(self) -> None:

        self._selected.clear()

        self._refresh_highlights()

        self._refresh_label()



    def _refresh_highlights(self) -> None:

        fmt_default = QTextCharFormat()

        fmt_selected = QTextCharFormat()

        fmt_selected.setBackground(QBrush(QColor("#2980b9")))

        fmt_selected.setForeground(QBrush(QColor("#ffffff")))

        for d in self._prev_highlighted - self._selected:

            self._cal.setDateTextFormat(d, fmt_default)

        for d in self._selected - self._prev_highlighted:

            self._cal.setDateTextFormat(d, fmt_selected)

        self._prev_highlighted = set(self._selected)



    def _refresh_label(self) -> None:

        n = len(self._selected)

        if n == 0:

            self._label.setText("No dates selected")

        elif n == 1:

            d = next(iter(self._selected))

            self._label.setText(f"1 date selected: {d.toString('yyyy-MM-dd')}")

        else:

            sample = ", ".join(sorted(d.toString("yyyy-MM-dd") for d in self._selected)[:5])

            suffix = "…" if n > 5 else ""

            self._label.setText(f"{n} dates selected: {sample}{suffix}")



    def selected_dates(self) -> set[QDate]:

        return set(self._selected)





class _DateFilterWidget(QWidget):

    """Compact toolbar widget: read-only display + button that opens the calendar dialog."""



    datesChanged = pyqtSignal()



    def __init__(self, parent=None):

        super().__init__(parent)

        self._dates: set[QDate] = set()

        layout = QHBoxLayout(self)

        layout.setContentsMargins(0, 0, 0, 0)

        layout.setSpacing(2)

        self._display = QLineEdit()

        self._display.setReadOnly(True)

        self._display.setPlaceholderText("no date filter…")

        self._display.setFixedWidth(220)

        self._btn = QPushButton("\U0001f4c5")

        self._btn.setToolTip("Open calendar picker")

        self._btn.setFixedWidth(28)

        self._btn.clicked.connect(self._open_picker)

        layout.addWidget(self._display)

        layout.addWidget(self._btn)



    def _open_picker(self) -> None:

        dlg = _MultiDateCalendarDialog(self._dates, self)

        if dlg.exec():

            self._dates = dlg.selected_dates()

            self._refresh_display()

            self.datesChanged.emit()



    def _refresh_display(self) -> None:

        n = len(self._dates)

        if n == 0:

            self._display.clear()

        elif n == 1:

            d = next(iter(self._dates))

            self._display.setText(d.toString("yyyy-MM-dd"))

        else:

            text = ", ".join(sorted(d.toString("yyyy-MM-dd") for d in self._dates))

            self._display.setText(text)



    def clear(self) -> None:

        if self._dates:

            self._dates.clear()

            self._refresh_display()

            self.datesChanged.emit()



    def selected_date_strings(self) -> set[str]:

        return {d.toString("yyyy-MM-dd") for d in self._dates}





# ---------------------------------------------------------------------------

# Background loader

# ---------------------------------------------------------------------------



class _LoadThread(QThread):

    done = pyqtSignal(list, list, list, dict)   # records, raw_sigs, raw_assigns, raw_flags_comments

    error = pyqtSignal(str)

    progress = pyqtSignal(int, str)       # percent (0-100), label



    def __init__(self, repo_root: Path):

        super().__init__()

        self._root = repo_root



    def run(self):

        try:

            self.progress.emit(0, "Scanning files…")

            records, sigs, assigns, flags_comments = load_repo(self._root)

            self.progress.emit(50, "Fetching git history…")

            fetch_git_info(self._root, records)

            self.progress.emit(100, "Done")

            self.done.emit(records, sigs, assigns, flags_comments)

        except Exception as exc:

            self.error.emit(str(exc))





# ---------------------------------------------------------------------------

# Row hover highlight — default delegate injecting State_MouseOver for whole row

# ---------------------------------------------------------------------------



class RowHighlightDelegate(QStyledItemDelegate):

    """Default item delegate that adds a row-level hover highlight.



    Injects ``State_MouseOver`` into the style option for every cell in

    the hovered row so Qt's style paints the hover background across the

    full row, not just the cell under the cursor.

    """



    def __init__(self, action_delegate: "ActionDelegate", parent=None):

        super().__init__(parent)

        self._ad = action_delegate



    def paint(self, painter: QPainter, option, index) -> None:

        if index.row() == self._ad._hover_row:

            opt = QStyleOptionViewItem(option)

            opt.state |= QStyle.StateFlag.State_MouseOver

            super().paint(painter, opt, index)

        else:

            super().paint(painter, option, index)





class SelectableTextDelegate(QStyledItemDelegate):

    """Read-only delegate for File/Contributor/Submitter/PR/Date/Last-Changed columns.



    Opens a transparent, frameless QLineEdit on click so the user can

    select and copy any portion of the cell text with the mouse.

    """



    def __init__(self, action_delegate, parent=None):

        super().__init__(parent)

        self._ad = action_delegate



    def paint(self, painter: QPainter, option, index) -> None:

        if self._ad and index.row() == self._ad._hover_row:

            opt = QStyleOptionViewItem(option)

            opt.state |= QStyle.StateFlag.State_MouseOver

            super().paint(painter, opt, index)

        else:

            super().paint(painter, option, index)



    def createEditor(self, parent, option, index):

        ed = QLineEdit(parent)

        ed.setReadOnly(True)

        ed.setFrame(False)

        ed.setStyleSheet(

            "QLineEdit { background: transparent; border: none; padding: 0 2px;"

            " selection-background-color: #f0a500; selection-color: #000000; }"

        )

        return ed



    def setEditorData(self, editor, index):

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""

        editor.setText(str(text))

        # Don't auto-select-all — let user decide what to select



    def setModelData(self, editor, model, index):

        pass  # read-only; never writes back



    def updateEditorGeometry(self, editor, option, index):

        editor.setGeometry(option.rect)





# ---------------------------------------------------------------------------

# Action delegate — Author + CLA + CC + GPL + MIT + CC0 quick-action buttons per row

# ---------------------------------------------------------------------------



_LICENSE_BTNS = [

    ("CLA",  "LicenseRef-BAR-CLA-1.0", QColor("#3b7d56")),

    ("CC",   "CC-BY-SA-4.0",           QColor("#7a6030")),

    ("GPL",  "GPL-2.0-or-later",        QColor("#5a4888")),

    ("MIT",  "MIT",                     QColor("#2e7d9c")),

    ("CC0",  "CC0-1.0",                 QColor("#7a8a2a")),   # public domain

]





# ---------------------------------------------------------------------------

# Status dot delegate — coloured circle per row in the Status column

# ---------------------------------------------------------------------------



class StatusDotDelegate(QStyledItemDelegate):

    """Paints a coloured dot for the Status column instead of text."""



    DOT_R = 6



    def __init__(self, parent=None):

        super().__init__(parent)

        self._action_delegate = None



    def set_action_delegate(self, ad) -> None:

        self._action_delegate = ad



    def paint(self, painter: QPainter, option, index) -> None:

        # Draw row background (selection, alternating colours, hover)

        opt = QStyleOptionViewItem(option)

        if self._action_delegate is not None and index.row() == self._action_delegate._hover_row:

            opt.state |= QStyle.StateFlag.State_MouseOver

        super().paint(painter, opt, index)



        rec = index.data(Qt.ItemDataRole.UserRole)

        status = rec.status if rec else ""

        color = STATUS_COLOR.get(status, QColor("#888888"))



        painter.save()

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        from PyQt6.QtCore import QPointF

        cx = option.rect.x() + option.rect.width() / 2

        cy = option.rect.y() + option.rect.height() / 2

        painter.setBrush(QBrush(color))

        painter.setPen(Qt.PenStyle.NoPen)

        painter.drawEllipse(QPointF(cx, cy), self.DOT_R, self.DOT_R)

        painter.restore()



    def sizeHint(self, option, index) -> QSize:

        return QSize(24, 24)





class ActionDelegate(QStyledItemDelegate):

    """Paints [Author] [CLA] [CC] [GPL] [MIT] [CC0] [✕] quick-action buttons per row.



    Clicks detected via viewport event-filter (see install_on).

    sig_assign_license carries (FileRecord, license_str).

    """



    sig_confirm_author = pyqtSignal(object)        # FileRecord

    sig_assign_license = pyqtSignal(object, str)   # FileRecord, license_str

    sig_clear_record   = pyqtSignal(object)        # FileRecord



    BTN_W  = 46

    BTN_H  = 18

    PAD    = 4

    CORNER = 0   # sharp right-angle corners



    _CLR_AUTHOR = QColor("#3d6e96")   # steel blue

    _CLR_CLEAR  = QColor("#555555")   # dark grey

    _CLR_WHITE  = QColor("#ffffff")



    # Author + one button per _LICENSE_BTNS entry

    N_BTNS = 1 + len(_LICENSE_BTNS)

    COL_W = BTN_W * N_BTNS + PAD * (N_BTNS + 1)



    def __init__(self, parent=None):

        super().__init__(parent)

        self._view = None

        self._hover_row = -1

        self._hover_col = -1

        # Drag-to-apply state (license buttons only)

        self._drag_mode: bool = False         # left button is held down

        self._drag_btn_col: int = -1          # which btn column locked (1..N_BTNS-1), -1 = not yet locked

        self._drag_apply: object = None       # ('assign', lic) | 'clear' | None

        self._drag_visited: set = set()       # model rows already acted on this drag



    def install_on(self, view: QTableView) -> None:

        self._view = view

        view.viewport().setMouseTracking(True)

        view.viewport().installEventFilter(self)



    # ------------------------------------------------------------------

    # Geometry helpers

    # ------------------------------------------------------------------



    def _btn_rects(self, cell_rect: QRect) -> list[QRect]:

        cy = cell_rect.y() + (cell_rect.height() - self.BTN_H) // 2

        rects = []

        x = cell_rect.x() + self.PAD

        for _ in range(self.N_BTNS):

            rects.append(QRect(x, cy, self.BTN_W, self.BTN_H))

            x += self.BTN_W + self.PAD

        return rects  # [author, CLA, CC, GPL, MIT, CC0]



    # ------------------------------------------------------------------

    # Painting

    # ------------------------------------------------------------------



    def paint(self, painter: QPainter, option, index) -> None:

        # Draw row background so Actions cell matches the rest of the row

        opt = QStyleOptionViewItem(option)

        if index.row() == self._hover_row:

            opt.state |= QStyle.StateFlag.State_MouseOver

        super().paint(painter, opt, index)



        painter.save()

        rects = self._btn_rects(option.rect)

        row = index.row()

        is_hover_row = (row == self._hover_row)

        rec = index.data(Qt.ItemDataRole.UserRole)

        author_active  = bool(rec and rec.author_confirmed)

        active_license = rec.license if rec else ""

        self._draw_btn(painter, rects[0], "Author", self._CLR_AUTHOR,

                       hovered=is_hover_row and self._hover_col == 0,

                       active=author_active)

        for i, (label, lic, color) in enumerate(_LICENSE_BTNS):

            self._draw_btn(painter, rects[i + 1], label, color,

                           hovered=is_hover_row and self._hover_col == i + 1,

                           active=active_license == lic)

        painter.restore()



    def _draw_btn(self, painter: QPainter, rect: QRect, text: str, color: QColor,

                  hovered: bool = False, active: bool = False) -> None:

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rf = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)

        if active:

            # Solid saturated fill, white text

            fill = color.lighter(120) if hovered else color

            painter.setPen(Qt.PenStyle.NoPen)

            painter.setBrush(QBrush(fill))

            painter.drawRect(rf)

            txt_color = self._CLR_WHITE

        elif hovered:

            # Medium-dark solid fill — clearly highlighted

            fill = color.darker(160)

            painter.setPen(Qt.PenStyle.NoPen)

            painter.setBrush(QBrush(fill))

            painter.drawRect(rf)

            txt_color = self._CLR_WHITE

        else:

            # Resting — very dark solid fill, light-tinted text, thin border

            fill = color.darker(340)

            painter.setPen(QPen(color.darker(160), 1.0))

            painter.setBrush(QBrush(fill))

            painter.drawRect(rf)

            txt_color = color.lighter(170)

        f = QFont()

        f.setPointSize(9)

        f.setBold(active)

        painter.setFont(f)

        painter.setPen(QPen(txt_color))

        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)



    def sizeHint(self, option, index) -> QSize:

        return QSize(self.COL_W, self.BTN_H + self.PAD * 2)



    # ------------------------------------------------------------------

    # Mouse event handling via viewport event filter

    # ------------------------------------------------------------------



    def _hit_btn_col(self, pos, rects) -> int:

        """Return button index (0..N_BTNS-1) hit by pos, or -1."""

        for i, r in enumerate(rects):

            if r.contains(pos):

                return i

        return -1



    def _update_hover(self, pos) -> None:

        if self._view is None:

            return

        idx = self._view.indexAt(pos)

        if idx.isValid():

            new_row = idx.row()

            if idx.column() == COL_ACTIONS:

                cell_rect = self._view.visualRect(idx)

                rects = self._btn_rects(cell_rect)

                new_col = self._hit_btn_col(pos, rects)

            else:

                new_col = -1

        else:

            new_row, new_col = -1, -1

        if new_row == self._hover_row and new_col == self._hover_col:

            return

        old_row = self._hover_row

        self._hover_row = new_row

        self._hover_col = new_col

        vm = self._view.model()

        col_count = vm.columnCount()

        if old_row >= 0:

            for c in range(col_count):

                self._view.update(vm.index(old_row, c))

        if new_row >= 0:

            for c in range(col_count):

                self._view.update(vm.index(new_row, c))



    def _apply_drag_action(self, rec) -> None:

        """Emit the pre-determined drag action signal for *rec*."""

        action = self._drag_apply

        if action == 'clear':

            self.sig_clear_record.emit(rec)

        elif isinstance(action, tuple) and action[0] == 'assign':

            self.sig_assign_license.emit(rec, action[1])



    def eventFilter(self, obj, event) -> bool:

        if not self._view:

            return False

        etype = event.type()

        if etype in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):

            try:

                pos = event.position().toPoint()

            except AttributeError:

                pos = event.pos()

            self._update_hover(pos)

            # Drag-to-apply: fire action for any new row the cursor enters

            if self._drag_mode:

                idx = self._view.indexAt(pos)

                if idx.isValid() and idx.column() == COL_ACTIONS:

                    row = idx.row()

                    cell_rect = self._view.visualRect(idx)

                    rects = self._btn_rects(cell_rect)

                    btn_col = self._hit_btn_col(pos, rects)

                    if 1 <= btn_col <= len(_LICENSE_BTNS):

                        # Lock to first button hit if not yet locked

                        if self._drag_btn_col == -1:

                            rec = idx.data(Qt.ItemDataRole.UserRole)

                            if rec is not None:

                                _label, lic, _color = _LICENSE_BTNS[btn_col - 1]

                                self._drag_apply = 'clear' if rec.license == lic else ('assign', lic)

                                self._drag_btn_col = btn_col

                                self._drag_visited.add(row)

                                self._apply_drag_action(rec)

                        elif btn_col == self._drag_btn_col and row not in self._drag_visited:

                            rec = idx.data(Qt.ItemDataRole.UserRole)

                            if rec is not None:

                                self._drag_visited.add(row)

                                self._apply_drag_action(rec)

        elif etype == QEvent.Type.Leave:

            if self._drag_mode:

                self._drag_mode = False

                self._drag_btn_col = -1

                self._drag_apply = None

                self._drag_visited.clear()

            if self._hover_row >= 0:

                old_row = self._hover_row

                self._hover_row = -1

                self._hover_col = -1

                vm = self._view.model()

                for c in range(vm.columnCount()):

                    self._view.update(vm.index(old_row, c))

        elif etype == QEvent.Type.MouseButtonPress:

            if event.button() == Qt.MouseButton.LeftButton:

                try:

                    pos = event.position().toPoint()

                except AttributeError:

                    pos = event.pos()

                self._drag_mode = True

                self._drag_btn_col = -1

                self._drag_apply = None

                self._drag_visited = set()

                idx = self._view.indexAt(pos)

                if idx.isValid() and idx.column() == COL_ACTIONS:

                    cell_rect = self._view.visualRect(idx)

                    rects = self._btn_rects(cell_rect)

                    btn_col = self._hit_btn_col(pos, rects)

                    rec = idx.data(Qt.ItemDataRole.UserRole)

                    if rec is not None and 1 <= btn_col <= len(_LICENSE_BTNS):

                        # Pressed directly on a button — lock immediately

                        _label, lic, _color = _LICENSE_BTNS[btn_col - 1]

                        self._drag_apply = 'clear' if rec.license == lic else ('assign', lic)

                        self._drag_btn_col = btn_col

                        self._drag_visited = {idx.row()}

                        self._apply_drag_action(rec)

                        return True

        elif etype == QEvent.Type.MouseButtonRelease:

            try:

                pos = event.position().toPoint()

            except AttributeError:

                pos = event.pos()

            # End any active drag

            if self._drag_mode:

                was_dragging = self._drag_btn_col >= 0

                self._drag_mode = False

                self._drag_btn_col = -1

                self._drag_apply = None

                self._drag_visited.clear()

                if was_dragging:

                    return True

            # Normal single-click: Author button (no drag) and fallback

            idx = self._view.indexAt(pos)

            if idx.isValid() and idx.column() == COL_ACTIONS:

                cell_rect = self._view.visualRect(idx)

                rects = self._btn_rects(cell_rect)

                rec = idx.data(Qt.ItemDataRole.UserRole)

                if rec is not None:

                    if rects[0].contains(pos):

                        self.sig_confirm_author.emit(rec)

                        return True

                    for i, (_label, lic, _color) in enumerate(_LICENSE_BTNS):

                        if rects[i + 1].contains(pos):

                            if rec.license == lic:

                                self.sig_clear_record.emit(rec)

                            else:

                                self.sig_assign_license.emit(rec, lic)

                            return True

        return False





# ---------------------------------------------------------------------------

# Inline edit delegate — QComboBox for License, QLineEdit for Contributor

# ---------------------------------------------------------------------------



def _recompute_status_for_author_license(rec) -> None:

    """Adjust rec.status after a license/contributor edit so that

    ``contributor without license`` becomes ``awaiting_license`` and

    ``contributor with license`` becomes ``confirmed``. Leaves merged/intent

    rows alone so they don't get demoted by a UI tweak.

    """

    if rec.status in ("merged", "intent"):

        return

    if rec.contributor and rec.license:

        rec.status = "confirmed"

    elif rec.contributor and not rec.license:

        rec.status = "awaiting_license"

    elif not rec.contributor and not rec.license:

        rec.status = "gap"

    # contributor empty + license set: leave whatever the user set



class InlineEditDelegate(QStyledItemDelegate):

    """Double-click inline editor for License (COL_LICENSE) and Contributor (COL_CONTRIBUTOR) cells.



    Enter commits, Escape cancels.  Mutations are pushed onto *undo_stack* and

    marked dirty.  Per-directory license memory is read/written via the two

    optional callables.

    """



    def __init__(self, undo_stack: QUndoStack, source_model_getter, refresh_fn,

                 dir_license_getter=None, dir_license_saver=None,

                 action_delegate=None, parent=None):

        super().__init__(parent)

        self._undo_stack = undo_stack

        self._get_src = source_model_getter   # callable() -> FileTableModel

        self._refresh_fn = refresh_fn

        self._dir_license_getter = dir_license_getter   # callable(top_dir) -> str

        self._dir_license_saver = dir_license_saver     # callable(top_dir, license) -> None

        self._ad = action_delegate                      # for row hover highlight



    # ------------------------------------------------------------------

    # Hover highlight — mirror RowHighlightDelegate behaviour

    # ------------------------------------------------------------------



    def paint(self, painter: QPainter, option, index) -> None:

        if self._ad and index.row() == self._ad._hover_row:

            opt = QStyleOptionViewItem(option)

            opt.state |= QStyle.StateFlag.State_MouseOver

            super().paint(painter, opt, index)

        else:

            super().paint(painter, option, index)



    # ------------------------------------------------------------------

    # Editor creation

    # ------------------------------------------------------------------



    def createEditor(self, parent, option, index):

        col = index.column()

        if col == COL_LICENSE:

            combo = QComboBox(parent)

            for lic in KNOWN_LICENSES:

                combo.addItem(lic, lic)

            combo.addItem("(none)", "")

            return combo

        if col == COL_CONTRIBUTOR:

            ed = QLineEdit(parent)

            ed.setPlaceholderText("GitHub handle (without @)")

            return ed

        if col == COL_FLAG:

            return MultiFlagCombo(parent)

        return super().createEditor(parent, option, index)



    def setEditorData(self, editor, index) -> None:

        rec = index.data(Qt.ItemDataRole.UserRole)

        col = index.column()

        if col == COL_LICENSE and isinstance(editor, QComboBox):

            current = rec.license if rec else ""

            idx = editor.findData(current)

            if idx >= 0:

                editor.setCurrentIndex(idx)

            elif not current and rec and self._dir_license_getter:

                # No existing license — suggest per-directory memory

                hint = self._dir_license_getter(rec.top_dir)

                if hint:

                    idx2 = editor.findData(hint)

                    if idx2 >= 0:

                        editor.setCurrentIndex(idx2)

        elif col == COL_CONTRIBUTOR and isinstance(editor, QLineEdit):

            if rec:

                if rec.contributor:

                    editor.setText(rec.contributor)

                elif rec.unitdef_author:

                    # Prefer lua model_author over git committer (artist != committer)

                    editor.setText(rec.unitdef_author)

                elif rec.proxy_submitter or rec.git_author:

                    editor.setText(rec.proxy_submitter or rec.git_author)

        elif col == COL_FLAG and isinstance(editor, MultiFlagCombo):

            if rec:

                editor.set_checked(rec.flags)

        else:

            super().setEditorData(editor, index)



    def setModelData(self, editor, model, index) -> None:

        rec = index.data(Qt.ItemDataRole.UserRole)

        if rec is None:

            return

        col = index.column()

        if col == COL_LICENSE and isinstance(editor, QComboBox):

            new_val = editor.currentData()

            field = "license"

        elif col == COL_CONTRIBUTOR and isinstance(editor, QLineEdit):

            new_val = editor.text().strip().lstrip("@")

            field = "contributor"

        elif col == COL_FLAG and isinstance(editor, MultiFlagCombo):

            new_val = editor.checked_flags()

            field = "flags"

        else:

            super().setModelData(editor, model, index)

            return

        old_val = getattr(rec, field, "")

        if new_val == old_val:

            return

        before = _RecordSnapshot.capture(rec)

        setattr(rec, field, new_val)

        if field == "license":

            # CC0 => contributor becomes the "public domain" placeholder; leaving

            # CC0 reverts a stale placeholder to the normal prefill (or clears it).

            rec.contributor = contributor_for_license(rec, new_val, rec.contributor)

        if field == "flags":

            rec.flags_comments_dirty = True

        else:

            rec.dirty = True

        if field == "contributor":

            rec.author_confirmed = bool(new_val)

        # Status transitions: contributor+license interplay for awaiting_license.

        if field in ("license", "contributor"):

            _recompute_status_for_author_license(rec)

        after = _RecordSnapshot.capture(rec)

        self._undo_stack.push(_RecordEditCommand(

            [rec], [before], [after], self._get_src(),

            self._refresh_fn, f"Inline {field}",

        ))

        if field == "license" and new_val and self._dir_license_saver:

            self._dir_license_saver(rec.top_dir, new_val)

        # Offer to auto-promote OTHER awaiting_license rows by same author.

        if field == "license" and new_val and rec.contributor and not is_public_domain_contributor(rec.contributor):

            mw = self.parent()

            # Walk up to MainWindow

            while mw is not None and not hasattr(mw, "_maybe_auto_promote_awaiting"):

                mw = mw.parent()

            if mw is not None:

                mw._maybe_auto_promote_awaiting(rec.contributor, new_val, exclude=rec)



    def updateEditorGeometry(self, editor, option, index) -> None:

        editor.setGeometry(option.rect)





# ---------------------------------------------------------------------------

# Undo/Redo infrastructure

# ---------------------------------------------------------------------------



class _RecordSnapshot:

    """Captures mutable fields of a FileRecord for undo/redo."""

    __slots__ = ("status", "license", "contributor", "dirty", "author_confirmed", "flags", "comments", "flags_comments_dirty", "extra")



    @classmethod

    def capture(cls, rec: "FileRecord") -> "_RecordSnapshot":

        s = cls.__new__(cls)

        s.status = rec.status

        s.license = rec.license

        s.contributor = rec.contributor

        s.dirty = rec.dirty

        s.author_confirmed = rec.author_confirmed

        s.flags = list(rec.flags or [])

        s.comments = [dict(c) if isinstance(c, dict) else c for c in (rec.comments or [])]

        s.flags_comments_dirty = rec.flags_comments_dirty
        s.extra = copy.deepcopy(rec.extra or {})

        return s



    def restore(self, rec: "FileRecord") -> None:

        rec.status = self.status

        rec.license = self.license

        rec.contributor = self.contributor

        rec.dirty = self.dirty

        rec.author_confirmed = self.author_confirmed

        rec.flags = list(self.flags or [])

        rec.comments = [dict(c) if isinstance(c, dict) else c for c in (self.comments or [])]

        rec.flags_comments_dirty = self.flags_comments_dirty
        rec.extra = copy.deepcopy(self.extra)





class _RecordEditCommand(QUndoCommand):

    """Undoable mutation of one or more FileRecord objects."""



    def __init__(

        self,

        records: list,

        befores: list,

        afters: list,

        source_model,

        refresh_fn,

        text: str = "Edit",

    ):

        super().__init__(text)

        self._records = records

        self._befores = befores

        self._afters = afters

        self._source_model = source_model

        self._refresh_fn = refresh_fn



    def redo(self) -> None:

        for rec, after in zip(self._records, self._afters):

            after.restore(rec)

        self._refresh_fn(self._records)



    def undo(self) -> None:

        for rec, before in zip(self._records, self._befores):

            before.restore(rec)

        self._refresh_fn(self._records)





# ---------------------------------------------------------------------------

# Help overlay — translucent keybinding reference (F1 / ?)

# ---------------------------------------------------------------------------



class _HelpOverlay(QWidget):

    """Semi-transparent overlay that lists all keyboard shortcuts."""



    _BINDINGS = [

        ("1",            "Confirm author for selected row(s)"),

        ("2",            "Assign CLA (LicenseRef-BAR-CLA-1.0) to selected"),

        ("3",            "Assign CC-BY-SA-4.0 to selected"),

        ("4",            "Assign GPL-2.0-or-later to selected"),

        ("N",            "Jump to next gap row in current view"),

        ("Shift+N",      "Jump to previous gap row in current view"),

        ("F1 / ?",       "Show / hide this help overlay"),

        ("Shift+F1",     "What's This? mode (hover over a widget for help)"),

        ("Ctrl+Z",       "Undo last edit"),

        ("Ctrl+Shift+Z", "Redo last edit"),

        ("Ctrl+D",       "Toggle detail panel"),

        ("Ctrl+L",       "Re-apply last bulk edit to selection"),

        ("Ctrl+O",       "Open repository…"),

        ("Ctrl+E",       "Edit selected row(s)…"),

        ("Ctrl+S",       "Save changes"),

        ("Ctrl+Shift+C", "Copy selected rows to clipboard as TSV"),

        ("Ctrl+Shift+E", "Export visible rows to CSV"),

        ("F5",           "Refresh data"),

        ("Esc",          "Dismiss this overlay"),

    ]



    def __init__(self, parent=None):

        super().__init__(parent)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setVisible(False)

        # Overlay must sit on top of all siblings

        self.raise_()



    def show_overlay(self) -> None:

        if self.parent():

            self.setGeometry(self.parent().rect())

        self.raise_()

        self.setVisible(True)

        self.update()



    def paintEvent(self, event) -> None:

        painter = QPainter(self)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)



        # Full-window dim

        painter.fillRect(self.rect(), QColor(0, 0, 0, 195))



        # Panel sizing

        panel_w = 520

        row_h = 26

        panel_h = 48 + len(self._BINDINGS) * row_h + 28

        px = (self.width() - panel_w) // 2

        py = (self.height() - panel_h) // 2



        # Panel background

        painter.setBrush(QBrush(QColor(28, 34, 48, 245)))

        painter.setPen(QPen(QColor(70, 110, 200), 1.5))

        painter.drawRoundedRect(px, py, panel_w, panel_h, 12, 12)



        # Title

        title_font = QFont()

        title_font.setPointSize(13)

        title_font.setBold(True)

        painter.setFont(title_font)

        painter.setPen(QColor(190, 215, 255))

        painter.drawText(QRect(px, py + 8, panel_w, 32), Qt.AlignmentFlag.AlignHCenter, "Keyboard Shortcuts")



        # Binding rows

        key_font = QFont("monospace", 9)

        desc_font = QFont()

        desc_font.setPointSize(10)

        for i, (key, desc) in enumerate(self._BINDINGS):

            y = py + 46 + i * row_h

            # Key badge

            key_rect = QRect(px + 16, y + 2, 140, row_h - 6)

            painter.setBrush(QBrush(QColor(48, 58, 82)))

            painter.setPen(QPen(QColor(90, 125, 190), 1))

            painter.drawRoundedRect(key_rect, 4, 4)

            painter.setFont(key_font)

            painter.setPen(QColor(170, 205, 255))

            painter.drawText(key_rect, Qt.AlignmentFlag.AlignCenter, key)

            # Description

            painter.setFont(desc_font)

            painter.setPen(QColor(215, 222, 235))

            painter.drawText(QRect(px + 168, y, panel_w - 184, row_h),

                             Qt.AlignmentFlag.AlignVCenter, desc)



        # Dismiss hint

        hint_font = QFont()

        hint_font.setPointSize(9)

        painter.setFont(hint_font)

        painter.setPen(QColor(120, 135, 160))

        painter.drawText(QRect(px, py + panel_h - 24, panel_w, 20),

                         Qt.AlignmentFlag.AlignHCenter, "Press F1, ?, or Esc to dismiss")



    def mousePressEvent(self, event) -> None:

        self.setVisible(False)



    def keyPressEvent(self, event) -> None:

        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F1, Qt.Key.Key_Question):

            self.setVisible(False)

        else:

            super().keyPressEvent(event)





# ---------------------------------------------------------------------------

# Detail panel — bottom collapsible panel (Ctrl+D) — Session 4

# ---------------------------------------------------------------------------



class _DetailPanel(QWidget):

    """Bottom detail panel for the focused row.



    Shows: full path, git author, dates, license text excerpt, clickable PR

    button, image thumbnail.  Ctrl+D toggles visibility.

    """



    sig_filter_author = pyqtSignal(str)   # "Show same author" clicked

    sig_set_flag      = pyqtSignal(object, list)        # FileRecord, new flags list

    sig_add_comment   = pyqtSignal(object, str)         # FileRecord, text

    sig_delete_comment = pyqtSignal(object, int)        # FileRecord, index



    _IMAGE_EXTS = frozenset([".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tga", ".dds"])



    _EXT_LABEL: dict[str, str] = {

        ".png":  "PNG Image",    ".jpg":  "JPEG Image",  ".jpeg": "JPEG Image",

        ".bmp":  "BMP Image",    ".gif":  "GIF Image",   ".webp": "WebP Image",

        ".tga":  "TGA Image",    ".dds":  "DDS Texture",

        ".s3o":  "3D Model",

        ".wav":  "Audio (WAV)",  ".ogg":  "Audio (OGG)", ".mp3":  "Audio (MP3)",

        ".glsl": "Shader (GLSL)", ".frag": "Frag Shader", ".vert": "Vert Shader",

        ".glslf": "Frag Shader", ".glslv": "Vert Shader",

        ".bos":  "Script (BOS)", ".cob":  "Script (COB)",

        ".ttf":  "Font (TTF)",   ".otf":  "Font (OTF)",  ".woff": "Font (WOFF)",

        ".lua":  "Script (Lua)",

    }



    _BAR_REPO = "https://github.com/beyond-all-reason/Beyond-All-Reason/blob/master"

    _BAR_UNIT_SITE = "https://www.beyondallreason.info/unit"

    _UNIT_DIRS = frozenset({"unitpics", "objects3d", "units", "unitmodels", "unittextures"})



    def __init__(self, parent=None):

        super().__init__(parent)

        self._repo_root: "Path | None" = None

        self._rec: "FileRecord | None" = None

        self.setObjectName("detailPanel")

        self.setStyleSheet(

            "#detailPanel { background: #2b2d31; border-left: 1px solid #3c3e44; }"

        )

        self.setMinimumWidth(240)

        self.setMaximumWidth(420)



        outer = QVBoxLayout(self)

        outer.setContentsMargins(12, 12, 12, 12)

        outer.setSpacing(8)



        # ── Thumbnail ────────────────────────────────────────────

        self._thumb_label = QLabel()

        self._thumb_label.setMinimumHeight(80)

        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._thumb_label.setStyleSheet(

            "background: #1e1f22; border: 1px solid #3c3e44;"

            " border-radius: 4px; color: #6d6f78; font-size: 10pt;"

        )

        outer.addWidget(self._thumb_label)



        # ── Path label ────────────────────────────────────────────

        self._path_label = QLabel()

        self._path_label.setStyleSheet(

            "color: #dbdee1; font-size: 11pt; font-family: monospace;"

        )

        self._path_label.setTextInteractionFlags(

            Qt.TextInteractionFlag.TextSelectableByMouse

        )

        self._path_label.setWordWrap(True)

        outer.addWidget(self._path_label)



        # ── File type / size / dimensions ─────────────────────────

        self._filetype_label = QLabel()

        self._filetype_label.setStyleSheet("color: #6d6f78; font-size: 11pt;")

        outer.addWidget(self._filetype_label)



        # ── Metadata table (HTML) ───────────────────────────────

        self._meta_label = QLabel()

        self._meta_label.setTextFormat(Qt.TextFormat.RichText)

        self._meta_label.setStyleSheet(

            "background: #1e1f22; border-radius: 4px; padding: 4px;"

            " font-size: 11pt;"

        )

        self._meta_label.setWordWrap(True)

        self._meta_label.setTextInteractionFlags(

            Qt.TextInteractionFlag.TextSelectableByMouse

        )

        outer.addWidget(self._meta_label)





        # ── Buttons ───────────────────────────────────────────────

        def _mk(text, clr):

            r, g, b_c = int(clr[1:3], 16), int(clr[3:5], 16), int(clr[5:7], 16)

            dim = f"#{r//4:02x}{g//4:02x}{b_c//4:02x}"

            btn = QPushButton(text)

            btn.setFixedHeight(28)

            btn.setStyleSheet(

                f"QPushButton {{ border:1px solid {clr}; border-radius:4px;"

                f" color:{clr}; background:{dim}; font-size:10pt; font-weight:600; padding:2px 6px; }}"

                f" QPushButton:hover {{ background:{clr}; color:#1e1f22; }}"

                f" QPushButton:disabled {{ border-color:#3c3e44; color:#4a4c52; background:#2b2d31; }}"

            )

            return btn



        # Row A: filter by author | copy path

        row_a = QHBoxLayout()

        row_a.setSpacing(4)

        self._author_btn = _mk("", "#3ba55c")

        self._author_btn.setVisible(False)

        self._author_btn.clicked.connect(self._emit_filter_author)

        self._copy_btn = _mk("Copy Path", "#72767d")

        self._copy_btn.setToolTip("Copy repo-relative path to clipboard")

        self._copy_btn.clicked.connect(self._copy_path)

        row_a.addWidget(self._author_btn, stretch=1)

        row_a.addWidget(self._copy_btn)

        outer.addLayout(row_a)



        # Row B: see PR | view on GitHub

        row_b = QHBoxLayout()

        row_b.setSpacing(4)

        self._pr_btn = _mk("", "#5865f2")

        self._pr_btn.setVisible(False)

        self._pr_btn.clicked.connect(self._open_pr)

        self._github_btn = _mk("View on GitHub", "#238636")

        self._github_btn.setToolTip("Browse file on GitHub (main branch)")

        self._github_btn.clicked.connect(self._open_github)

        row_b.addWidget(self._pr_btn, stretch=1)

        row_b.addWidget(self._github_btn)

        outer.addLayout(row_b)



        # Row C: open file location

        self._open_loc_btn = _mk("Open File Location", "#72767d")

        self._open_loc_btn.setToolTip("Reveal file in system file manager")

        self._open_loc_btn.clicked.connect(self._open_file_location)

        outer.addWidget(self._open_loc_btn)



        # Row D: unit library

        self._unitlib_btn = _mk("Unit Library", "#f0b232")

        self._unitlib_btn.setToolTip("Open unit page on beyondallreason.info")

        self._unitlib_btn.setVisible(False)

        self._unitlib_btn.clicked.connect(self._open_unitlib)

        outer.addWidget(self._unitlib_btn)



        # Row E: open unitdef lua

        self._unitdef_btn = _mk("Open Unitdef", "#4e9cdd")

        self._unitdef_btn.setToolTip("Reveal unitdef .lua file in file manager")

        self._unitdef_btn.setVisible(False)

        self._unitdef_btn.clicked.connect(self._open_unitdef)

        outer.addWidget(self._unitdef_btn)



        # ── Dirty warning ───────────────────────────────────────────

        self._dirty_label = QLabel("⚠  Unsaved changes")

        self._dirty_label.setStyleSheet(

            "color: #f0b232; font-size: 11pt; font-weight: bold;"

            " background: transparent;"

        )

        self._dirty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._dirty_label.setVisible(False)

        outer.addWidget(self._dirty_label)



        # ── Flag selector ───────────────────────────────────────────

        _flag_row = QHBoxLayout()

        _flag_row.setSpacing(4)

        _flag_lbl = QLabel("Flags:")

        _flag_lbl.setStyleSheet("color:#dbdee1; font-size:11pt;")

        self._flag_combo = MultiFlagCombo()

        self._flag_combo.sig_changed.connect(self._on_flag_combo_changed)

        _flag_row.addWidget(_flag_lbl)

        _flag_row.addWidget(self._flag_combo, stretch=1)

        outer.addLayout(_flag_row)



        # ── Comments ────────────────────────────────────────────────

        _cm_header = QLabel("Comments")

        _cm_header.setStyleSheet("color:#dbdee1; font-size:11pt; font-weight:600; padding-top:4px;")

        outer.addWidget(_cm_header)



        self._comments_list = QListWidget()

        self._comments_list.setStyleSheet(

            "QListWidget { background:#1e1f22; border:1px solid #3c3e44; border-radius:4px;"

            " color:#dbdee1; font-size:10pt; }"

            " QListWidget::item { padding:4px; border-bottom:1px solid #2b2d31; }"

        )

        self._comments_list.setMaximumHeight(140)

        self._comments_list.itemDoubleClicked.connect(self._on_comment_double_clicked)

        outer.addWidget(self._comments_list)



        self._new_comment_edit = QTextEdit()

        self._new_comment_edit.setPlaceholderText("Add a comment… (Ctrl+Enter to post)")

        self._new_comment_edit.setMaximumHeight(60)

        self._new_comment_edit.setStyleSheet(

            "QTextEdit { background:#1e1f22; border:1px solid #3c3e44; border-radius:4px;"

            " color:#dbdee1; font-size:10pt; padding:4px; }"

        )

        outer.addWidget(self._new_comment_edit)



        _cm_btn_row = QHBoxLayout()

        _cm_btn_row.setSpacing(4)

        self._post_comment_btn = _mk("Post Comment", "#3ba55c")

        self._post_comment_btn.clicked.connect(self._on_post_comment)

        self._delete_comment_btn = _mk("Delete Selected", "#ed4245")

        self._delete_comment_btn.clicked.connect(self._on_delete_selected_comment)

        _cm_btn_row.addWidget(self._post_comment_btn, stretch=1)

        _cm_btn_row.addWidget(self._delete_comment_btn)

        outer.addLayout(_cm_btn_row)



        # Ctrl+Enter posts a comment from the textedit

        QShortcut(QKeySequence("Ctrl+Return"), self._new_comment_edit,

                  activated=self._on_post_comment)



        outer.addStretch()



    # ------------------------------------------------------------------

    # Public API

    # ------------------------------------------------------------------



    def set_repo_root(self, root: "Path | None") -> None:

        self._repo_root = root



    def rebuild_flag_choices(self) -> None:

        """Repopulate the flags combo after the flag registry changed."""

        self._flag_combo.blockSignals(True)

        self._flag_combo.repopulate()

        self._flag_combo.set_checked(self._rec.flags if self._rec else [])

        self._flag_combo.blockSignals(False)

    def refresh_flag_and_comments(self) -> None:

        """Re-sync the flag combo + comments list against ``self._rec``.

        Called by MainWindow after it mutates flag/comments so the panel

        reflects the current truth without rebuilding the whole record view.

        """

        rec = self._rec

        if rec is None:

            return

        self._flag_combo.blockSignals(True)

        self._flag_combo.set_checked(rec.flags)

        self._flag_combo.blockSignals(False)

        self._comments_list.clear()

        for entry in (rec.comments or []):

            if isinstance(entry, dict):

                who = entry.get("author") or "anon"

                when = entry.get("date") or ""

                txt = entry.get("text") or ""

                self._comments_list.addItem(f"[{when}] {who}: {txt}")

            else:

                self._comments_list.addItem(str(entry))



    # ------------------------------------------------------------------

    # Comments / flag UI handlers

    # ------------------------------------------------------------------



    def _on_flag_combo_changed(self) -> None:

        if self._rec is None:

            return

        new_flags = self._flag_combo.checked_flags()

        if new_flags == (self._rec.flags or []):

            return

        self.sig_set_flag.emit(self._rec, new_flags)



    def _on_post_comment(self) -> None:

        if self._rec is None:

            return

        text = self._new_comment_edit.toPlainText().strip()

        if not text:

            return

        self._new_comment_edit.clear()

        self.sig_add_comment.emit(self._rec, text)



    def _on_delete_selected_comment(self) -> None:

        if self._rec is None:

            return

        row = self._comments_list.currentRow()

        if row < 0:

            return

        self.sig_delete_comment.emit(self._rec, row)



    def _on_comment_double_clicked(self, _item) -> None:

        # Double-click = quick delete after confirmation

        if self._rec is None:

            return

        row = self._comments_list.currentRow()

        if row < 0:

            return

        reply = QMessageBox.question(

            self, "Delete comment?", "Remove this comment?",

            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,

            QMessageBox.StandardButton.No,

        )

        if reply == QMessageBox.StandardButton.Yes:

            self.sig_delete_comment.emit(self._rec, row)



    def show_record(self, rec: "FileRecord") -> None:

        self._rec = rec

        if rec is None:

            self.clear()

            return



        from pathlib import PurePosixPath as _PP

        ext = _PP(rec.path).suffix.lower()



        # Path

        self._path_label.setText(rec.path)



        # File type + size (dimensions appended later by _load_thumbnail)

        type_label = self._EXT_LABEL.get(ext, ext.upper().lstrip(".") or "File")

        size_str = ""

        if self._repo_root:

            try:

                sz = (self._repo_root / rec.path).stat().st_size

                if sz >= 1_048_576:

                    size_str = f" • {sz / 1_048_576:.1f} MB"

                elif sz >= 1024:

                    size_str = f" • {sz / 1024:.1f} KB"

                else:

                    size_str = f" • {sz} B"

            except Exception:

                pass

        self._filetype_label.setText(type_label + size_str)



        # Metadata HTML table

        def _row(label, value, color="#b5bac1"):

            if not value:

                return ""

            td1 = '<td style="color:#6d6f78;padding-right:8px;white-space:nowrap;">'

            td2 = f'<td style="color:{color};">'

            return f"<tr>{td1}{label}</td>{td2}{value}</td></tr>"



        contrib   = rec.contributor or ""

        git_a     = rec.git_author or ""

        unitdef   = rec.unitdef_author or ""

        submitter = rec.proxy_submitter or ""



        confirmed_badge = ' <span style="color:#3ba55c;">&#10003;</span>' if rec.author_confirmed else ""

        git_a_row = git_a if git_a and git_a.lower() != contrib.lower() else ""



        _STATUS_CLR = {

            "gap": "#ed4245", "intent": "#f0b232",

            "confirmed": "#3ba55c", "merged": "#5865f2",

        }

        _STATUS_LBL = {

            "gap": "Gap", "intent": "Intent",

            "confirmed": "Confirmed", "merged": "Merged",

        }

        status_clr = _STATUS_CLR.get(rec.status, "#b5bac1")

        status_lbl = _STATUS_LBL.get(rec.status, rec.status)



        html = (

            '<table cellspacing="0" cellpadding="0">'

            + _row("Contributor:", f"{contrib}{confirmed_badge}" if contrib else "")

            + _row("Git author:", git_a_row)

            + _row("Unit def:", unitdef)

            + _row("Submitter:", submitter)

            + _row("Date:", rec.date)

            + _row("Last changed:", rec.last_changed)

            + _row("Status:", status_lbl, status_clr)

            + _row("License:", rec.license)

            + _row("Top dir:", rec.top_dir)

            + '</table>'

        )

        self._meta_label.setText(html)



        # PR button

        pr_url = rec.pr_url or (

            f"https://github.com/beyond-all-reason/Beyond-All-Reason/pull/{rec.pr}"

            if rec.pr and rec.pr.isdigit() else ""

        )

        if pr_url:

            self._pr_btn.setText(f"PR #{rec.pr}" if rec.pr else "See PR")

            self._pr_btn.setProperty("_pr_url", pr_url)

            self._pr_btn.setVisible(True)

        elif rec.commit_url:

            short = rec.commit_hash[:7] if rec.commit_hash else ""

            self._pr_btn.setText(f"Commit {short}" if short else "See Commit")

            self._pr_btn.setProperty("_pr_url", rec.commit_url)

            self._pr_btn.setVisible(True)

        else:

            self._pr_btn.setVisible(False)





        # Author filter button — most specific author first

        primary = contrib or git_a or unitdef or submitter

        if primary:

            self._author_btn.setText(f"Files by {primary}")

            self._author_btn.setToolTip(f"Filter table to all files by {primary}")

            self._author_btn.setVisible(True)

        else:

            self._author_btn.setVisible(False)



        # Dirty warning

        self._dirty_label.setVisible(getattr(rec, "dirty", False))



        # Unit library + unitdef lua buttons.  Resolve the asset to its unit

        # (armsilo_dead_8_8_aoplane -> armsilo) with the same key candidates the

        # unitdef-author correlation uses, so wrecks/decals link like unitpics.

        # Both buttons show only when a matching units/<unit>.lua exists.

        from pathlib import PurePosixPath as _PP2

        _stem = _PP2(rec.path).stem.lower()

        _unitdef_path = None

        if self._repo_root and _stem and rec.top_dir in self._UNIT_DIRS:

            _lua_by_stem: dict = {}

            for _lua in (self._repo_root / "units").rglob("*.lua"):

                _lua_by_stem.setdefault(_lua.stem.lower(), _lua)

            for _key in unit_key_candidates(_stem):

                if _key in _lua_by_stem:

                    _stem, _unitdef_path = _key, _lua_by_stem[_key]

                    break

        if _unitdef_path:

            self._unitlib_btn.setText(f"Unit Library • {_stem}")

            self._unitlib_btn.setProperty("_unit_url", f"{self._BAR_UNIT_SITE}/{_stem}")

            self._unitlib_btn.setVisible(True)

        else:

            self._unitlib_btn.setVisible(False)



        if _unitdef_path:

            self._unitdef_btn.setProperty("_unitdef_path", str(_unitdef_path))

            self._unitdef_btn.setToolTip(str(_unitdef_path))

            self._unitdef_btn.setVisible(True)

        else:

            self._unitdef_btn.setVisible(False)



        # Thumbnail (lazy — only when panel is visible)

        if self.isVisible():

            self._load_thumbnail(rec)

        else:

            self._thumb_label.clear()

            self._thumb_label.setText("")



        # Flag combo + comments list reflect this record

        self.refresh_flag_and_comments()



    def clear(self) -> None:

        self._rec = None

        self._path_label.clear()

        self._filetype_label.clear()

        self._meta_label.clear()

        self._pr_btn.setVisible(False)

        self._author_btn.setVisible(False)

        self._dirty_label.setVisible(False)

        self._unitlib_btn.setVisible(False)

        self._unitdef_btn.setVisible(False)

        self._thumb_label.clear()

        self._flag_combo.blockSignals(True)

        self._flag_combo.set_checked([])

        self._flag_combo.blockSignals(False)

        self._comments_list.clear()

        self._new_comment_edit.clear()

        self._thumb_label.setText("")



    def resizeEvent(self, event) -> None:

        super().resizeEvent(event)

        w = self._thumb_label.width()

        if w > 0:

            self._thumb_label.setFixedHeight(w)

            if self._rec is not None and self.isVisible():

                self._load_thumbnail(self._rec)



    def refresh_thumbnail(self) -> None:

        """Call after making the panel visible to load the thumbnail lazily."""

        if self._rec is not None:

            self._load_thumbnail(self._rec)



    # ------------------------------------------------------------------

    # Private helpers

    # ------------------------------------------------------------------




    def _load_thumbnail(self, rec: "FileRecord") -> None:

        from pathlib import PurePosixPath

        self._thumb_label.clear()

        self._thumb_label.setText("")

        ext = PurePosixPath(rec.path).suffix.lower()

        if ext == ".s3o" and self._repo_root:

            stem = PurePosixPath(rec.path).stem

            for _up_ext in (".png", ".dds", ".jpg", ".jpeg"):

                unitpic_path = self._repo_root / "unitpics" / (stem + _up_ext)

                if not unitpic_path.exists():

                    continue

                if _up_ext == ".dds":

                    try:

                        import io as _io

                        from PIL import Image

                        _img = Image.open(unitpic_path).convert("RGBA")

                        _buf = _io.BytesIO()

                        _img.save(_buf, format="PNG")

                        px = QPixmap()

                        px.loadFromData(_buf.getvalue())

                    except Exception:

                        continue

                else:

                    px = QPixmap(str(unitpic_path))

                if not px.isNull():

                    _lw = self._thumb_label.width() or 200

                    _lh = self._thumb_label.height()

                    scaled = px.scaled(_lw, _lh, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)

                    if scaled.width() > _lw or scaled.height() > _lh:

                        scaled = scaled.copy((scaled.width() - _lw) // 2, (scaled.height() - _lh) // 2, _lw, _lh)

                    self._thumb_label.setPixmap(scaled)

                    return

            self._thumb_label.setText("no unitpic")

            return

        if ext not in self._IMAGE_EXTS or not self._repo_root:

            return

        abs_path = self._repo_root / rec.path

        if not abs_path.exists():

            return

        if ext == ".dds":

            try:

                import io as _io

                from PIL import Image

                img = Image.open(abs_path).convert("RGBA")

                buf = _io.BytesIO()

                img.save(buf, format="PNG")

                px = QPixmap()

                px.loadFromData(buf.getvalue())

            except Exception:

                self._thumb_label.setText("no preview")

                return

        else:

            px = QPixmap(str(abs_path))

        if px.isNull():

            self._thumb_label.setText("no preview")

            return

        self._update_filetype_dimensions(px.width(), px.height())

        _lw = self._thumb_label.width() or 200

        _lh = self._thumb_label.height()

        scaled = px.scaled(_lw, _lh, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)

        if scaled.width() > _lw or scaled.height() > _lh:

            scaled = scaled.copy((scaled.width() - _lw) // 2, (scaled.height() - _lh) // 2, _lw, _lh)

        self._thumb_label.setPixmap(scaled)



    def _open_pr(self) -> None:

        url = self._pr_btn.property("_pr_url") or ""

        if url:

            QDesktopServices.openUrl(QUrl(url))



    def _emit_filter_author(self) -> None:

        if self._rec:

            author = (self._rec.contributor or self._rec.git_author

                      or self._rec.unitdef_author or self._rec.proxy_submitter)

            if author:

                self.sig_filter_author.emit(author)



    def _copy_path(self) -> None:

        if self._rec:

            from PyQt6.QtWidgets import QApplication as _QApp

            _QApp.clipboard().setText(self._rec.path)



    def _open_github(self) -> None:

        if self._rec:

            QDesktopServices.openUrl(QUrl(f"{self._BAR_REPO}/{self._rec.path}"))



    def _open_unitlib(self) -> None:

        url = self._unitlib_btn.property("_unit_url")

        if url:

            QDesktopServices.openUrl(QUrl(url))



    def _open_unitdef(self) -> None:

        fpath = self._unitdef_btn.property("_unitdef_path")

        if not fpath:

            return

        try:

            lua_text = Path(fpath).read_text(encoding="utf-8", errors="replace")

        except Exception as exc:

            QMessageBox.warning(self, "Unitdef", f"Could not read file:\n{exc}")

            return

        dlg = QDialog(self)

        dlg.setWindowTitle(f"Unitdef — {Path(fpath).name}")

        dlg.resize(760, 620)

        layout = QVBoxLayout(dlg)

        layout.setContentsMargins(20, 16, 20, 12)

        layout.setSpacing(12)

        view = QTextEdit()

        view.setReadOnly(True)

        view.setPlainText(lua_text)

        _f = view.font()

        _f.setFamily("Consolas, Courier New, monospace")

        _f.setPointSize(10)

        view.setFont(_f)

        view.document().setDocumentMargin(16)

        layout.addWidget(view)

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)

        btn.rejected.connect(dlg.accept)

        layout.addWidget(btn)

        dlg.exec()



    def _open_file_location(self) -> None:

        if self._rec and self._repo_root:

            parent_dir = (self._repo_root / self._rec.path).parent

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(parent_dir)))



    def _update_filetype_dimensions(self, w: int, h: int) -> None:

        """Append WxH to the filetype label (strips existing dim suffix first)."""

        if w and h:

            txt = self._filetype_label.text()

            if "×" in txt:

                txt = txt.rsplit(" • ", 1)[0]

            self._filetype_label.setText(f"{txt} • {w}×{h}")





# ---------------------------------------------------------------------------

# Dashboard — Session 5

# ---------------------------------------------------------------------------



class _DashboardDirDelegate(QStyledItemDelegate):

    """Paints a per-directory row: name + count text on top, segmented progress bar below."""



    ROW_H = 38

    BAR_H = 7



    _CLR_CONFIRMED = QColor("#27ae60")

    _CLR_INTENT    = QColor("#e67e22")

    _CLR_MERGED    = QColor("#3498db")

    _CLR_EMPTY     = QColor("#2a3550")



    def paint(self, painter: QPainter, option, index) -> None:

        opt = QStyleOptionViewItem(option)

        self.initStyleOption(opt, index)

        super().paint(painter, opt, index)



        data = index.data(Qt.ItemDataRole.UserRole)

        if not data:

            return



        r = option.rect

        total     = data.get("total", 0)

        gap       = data.get("gap", 0)

        intent    = data.get("intent", 0)

        confirmed = data.get("confirmed", 0)

        merged    = data.get("merged", 0)

        done      = confirmed + merged

        pct       = (done / total * 100) if total else 0.0



        painter.save()



        # ── Row 1: dir name (left) + count summary (right) ───────────

        name_rect = QRect(r.x() + 5, r.y() + 4, r.width() - 10, 16)

        name_font = QFont()

        name_font.setPointSize(9)

        name_font.setBold(True)

        painter.setFont(name_font)

        painter.setPen(QColor("#c8d4e8"))

        dir_label = (data.get("dir") + "/") if data.get("dir") else "All art files"

        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, dir_label)



        count_font = QFont()

        count_font.setPointSize(7)

        painter.setFont(count_font)

        painter.setPen(QColor("#e08080") if gap else QColor("#8899bb"))

        counts_text = f"g:{gap} i:{intent} c:{confirmed}  {pct:.0f}%"

        painter.drawText(name_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, counts_text)



        # ── Row 2: segmented progress bar ────────────────────────────

        bar_y    = r.y() + 24

        bar_rect = QRect(r.x() + 5, bar_y, r.width() - 10, self.BAR_H)

        painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(QBrush(self._CLR_EMPTY))

        painter.drawRoundedRect(bar_rect, 3, 3)



        if total > 0:

            w = bar_rect.width()

            confirmed_w = int(w * confirmed / total)

            merged_w    = int(w * merged    / total)

            intent_w    = int(w * intent    / total)

            x = bar_rect.x()

            for seg_w, color in (

                (confirmed_w, self._CLR_CONFIRMED),

                (merged_w,    self._CLR_MERGED),

                (intent_w,    self._CLR_INTENT),

            ):

                if seg_w > 0:

                    painter.setBrush(QBrush(color))

                    painter.drawRect(QRect(x, bar_y, seg_w, self.BAR_H))

                    x += seg_w



        painter.restore()



    def sizeHint(self, option, index) -> QSize:

        return QSize(option.rect.width(), self.ROW_H)





class _DashboardPanel(QWidget):

    """Session 5: per-directory progress bars, session stats, new-gaps banner.



    Emits sig_jump_to_dir(top_dir) when a directory row is clicked.

    Pass top_dir="" to mean "all".

    """



    sig_jump_to_dir = pyqtSignal(str)



    def __init__(self, parent=None):

        super().__init__(parent)

        layout = QVBoxLayout(self)

        layout.setContentsMargins(4, 4, 4, 4)

        layout.setSpacing(4)



        # New-gaps notification banner (hidden until first load shows a delta)

        self._banner = QLabel()

        self._banner.setWordWrap(True)

        self._banner.setStyleSheet(

            "QLabel { background: #3a2000; border: 1px solid #e67e22;"

            " border-radius: 4px; color: #e67e22; padding: 4px 6px; font-size: 9pt; }"

        )

        self._banner.setVisible(False)

        layout.addWidget(self._banner)



        # Overall completion stats

        self._stats_label = QLabel("No data loaded")

        self._stats_label.setStyleSheet("font-size: 9pt; color: #c8d4e8; padding: 2px;")

        self._stats_label.setWordWrap(True)

        layout.addWidget(self._stats_label)



        # Session counter

        self._session_label = QLabel("Confirmed this session: 0")

        self._session_label.setStyleSheet("font-size: 8pt; color: #8899bb; padding: 2px;")

        layout.addWidget(self._session_label)



        # Priority list header

        hdr_lbl = QLabel("Priority (most gaps first) — click to jump:")

        hdr_lbl.setStyleSheet("font-weight: bold; font-size: 8pt; color: #a0b0cc; padding: 2px 0;")

        layout.addWidget(hdr_lbl)



        # Directory progress list

        self._dir_list = QListWidget()

        self._dir_list.setItemDelegate(_DashboardDirDelegate(self._dir_list))

        self._dir_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self._dir_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._dir_list.setUniformItemSizes(True)

        self._dir_list.itemClicked.connect(self._on_item_clicked)

        layout.addWidget(self._dir_list, stretch=1)



    def refresh(self, records: list, session_confirmed: int,

                new_gaps_delta: "int | None") -> None:

        """Rebuild dashboard from *records*.

        *new_gaps_delta=None* keeps the banner unchanged (mutation path).

        """

        from collections import defaultdict

        counts: dict = defaultdict(lambda: {"gap": 0, "intent": 0, "confirmed": 0, "merged": 0})

        for r in records:

            counts[r.top_dir][r.status] = counts[r.top_dir].get(r.status, 0) + 1



        total     = len(records)

        gaps      = sum(1 for r in records if r.status == "gap")

        intent    = sum(1 for r in records if r.status == "intent")

        confirmed = sum(1 for r in records if r.status == "confirmed")

        merged    = sum(1 for r in records if r.status == "merged")

        done      = confirmed + merged

        pct       = (done / total * 100) if total else 0.0



        self._stats_label.setText(

            f"{total} files  •  {gaps} gaps  •  {intent} intent  •  "

            f"{confirmed} confirmed  •  {done}/{total} done ({pct:.0f}%)"

        )

        self._session_label.setText(f"Confirmed this session: {session_confirmed}")



        # Banner — only update when caller supplies a delta (load events)

        if new_gaps_delta is not None:

            if new_gaps_delta > 0:

                self._banner.setText(f"\u26a0\u2002{new_gaps_delta} new gap(s) vs. last audit")

                self._banner.setVisible(True)

            elif new_gaps_delta < 0:

                self._banner.setText(f"\u2713\u2002{-new_gaps_delta} fewer gap(s) vs. last audit")

                self._banner.setVisible(True)

            else:

                self._banner.setVisible(False)



        # Priority list: dirs sorted by gap count descending

        self._dir_list.clear()

        dir_data = sorted(

            [

                {

                    "dir":       top_dir,

                    "gap":       c.get("gap", 0),

                    "intent":    c.get("intent", 0),

                    "confirmed": c.get("confirmed", 0),

                    "merged":    c.get("merged", 0),

                    "total":     sum(c.values()),

                }

                for top_dir, c in counts.items()

            ],

            key=lambda d: (-d["gap"], d["dir"]),

        )

        for d in dir_data:

            item = QListWidgetItem()

            item.setData(Qt.ItemDataRole.UserRole, d)

            item.setSizeHint(QSize(0, _DashboardDirDelegate.ROW_H + 2))

            self._dir_list.addItem(item)



    def _on_item_clicked(self, item: QListWidgetItem) -> None:

        data = item.data(Qt.ItemDataRole.UserRole)

        if data is not None:

            self.sig_jump_to_dir.emit(data.get("dir", ""))





# ---------------------------------------------------------------------------

# Filter chips bar — dismissible active-filter badges (Session 6)

# ---------------------------------------------------------------------------



class _FilterChipsBar(QWidget):

    """Horizontal row of dismissible filter chips shown when any filter is active.



    Each chip is a QPushButton with "label  ✕".  Clicking it emits chip_clear(key).

    """



    chip_clear = pyqtSignal(str)   # emitted with the filter key when ✕ is clicked



    def __init__(self, parent=None):

        super().__init__(parent)

        self.setObjectName("chipsBar")

        self.setStyleSheet("#chipsBar { background: #111822; border-bottom: 1px solid #2a3550; }")

        self.setFixedHeight(28)

        self.setVisible(False)



        layout = QHBoxLayout(self)

        layout.setContentsMargins(6, 0, 6, 0)

        layout.setSpacing(4)



        lbl = QLabel("Active filters:")

        lbl.setStyleSheet("color: #7788aa; font-size: 8pt;")

        layout.addWidget(lbl)



        self._chip_area = QHBoxLayout()

        self._chip_area.setSpacing(4)

        layout.addLayout(self._chip_area)

        layout.addStretch()



    def update_chips(self, chips: list[tuple[str, str]]) -> None:

        """Rebuild chip buttons from *chips* = list of (key, display_label)."""

        while self._chip_area.count():

            item = self._chip_area.takeAt(0)

            if item.widget():

                item.widget().deleteLater()

        for key, label in chips:

            btn = QPushButton(f"{label}  ✕")

            btn.setFixedHeight(20)

            btn.setStyleSheet(

                "QPushButton { border: 1px solid #445577; border-radius: 9px;"

                " color: #99bbdd; background: #1c2840; padding: 1px 8px; font-size: 8pt; }"

                " QPushButton:hover { background: #aa3333; color: white; border-color: #cc4444; }"

            )

            btn.clicked.connect(lambda _checked, k=key: self.chip_clear.emit(k))

            self._chip_area.addWidget(btn)

        self.setVisible(len(chips) > 0)





# ---------------------------------------------------------------------------

# Column visibility / order dialog — Session 8

# ---------------------------------------------------------------------------



class _ColumnDialog(QDialog):

    """Check columns visible/hidden; drag rows to reorder; Apply pushes to QHeaderView."""



    def __init__(self, header: QHeaderView, col_names: list[str], parent=None):

        super().__init__(parent)

        self.setWindowTitle("Column Visibility & Order")

        self.setMinimumWidth(300)

        self.setMinimumHeight(380)

        self._header = header

        self._col_names = col_names



        layout = QVBoxLayout(self)

        lbl = QLabel("✓ Check to show  •  Drag rows to reorder")

        lbl.setStyleSheet("color: #8899bb; font-size: 8pt; padding-bottom: 4px;")

        layout.addWidget(lbl)



        self._list = QListWidget()

        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)



        # Populate in current visual order

        for vis_idx in range(header.count()):

            log_idx = header.logicalIndex(vis_idx)

            item = QListWidgetItem(col_names[log_idx])

            item.setData(Qt.ItemDataRole.UserRole, log_idx)

            item.setFlags(

                item.flags()

                | Qt.ItemFlag.ItemIsUserCheckable

                | Qt.ItemFlag.ItemIsDragEnabled

            )

            item.setCheckState(

                Qt.CheckState.Unchecked

                if header.isSectionHidden(log_idx)

                else Qt.CheckState.Checked

            )

            self._list.addItem(item)



        layout.addWidget(self._list)



        btns = QDialogButtonBox(

            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel

        )

        btns.accepted.connect(self._apply)

        btns.rejected.connect(self.reject)

        layout.addWidget(btns)



    def _apply(self) -> None:

        """Apply visibility + reorder to the header view."""

        for dialog_pos in range(self._list.count()):

            item = self._list.item(dialog_pos)

            log_idx = item.data(Qt.ItemDataRole.UserRole)

            hidden = item.checkState() != Qt.CheckState.Checked

            self._header.setSectionHidden(log_idx, hidden)

            current_vis = self._header.visualIndex(log_idx)

            if current_vis != dialog_pos:

                self._header.moveSection(current_vis, dialog_pos)

        self.accept()





# ---------------------------------------------------------------------------

# Main window

# ---------------------------------------------------------------------------



class MainWindow(QMainWindow):



    def __init__(self, repo_root: Path | None = None):

        super().__init__()

        self.setWindowTitle("BAR License Auditor")

        self.resize(1280, 760)



        self._repo_root: Path | None = None

        self._records: list[FileRecord] = []

        self._raw_sigs: list[dict] = []

        self._raw_assigns: list[dict] = []

        self._raw_flags_comments: dict[str, dict] = {}

        self._flag_defs_dirty = False   # custom flag defs changed since last save

        self._pending_commit_records: list[FileRecord] = []

        self._pending_commit_before: list[dict] | None = None

        self._pending_commit_fc_records: list[FileRecord] = []

        self._pending_commit_fc_before: dict[str, dict] = {}

        self._settings = QSettings("BAR", "LicenseAuditor")

        self._undo_stack = QUndoStack(self)

        self._last_edit: dict = {}  # cached values for Ctrl+L "apply last edit"

        self._load_confirmed = 0      # confirmed count at load time (session baseline)

        self._session_confirmed = 0   # records confirmed since last load

        self._dark_mode: bool = False

        self._compact_mode: bool = False

        # Capture the initial (system) palette so we can restore it for light mode

        self._light_palette: QPalette = QApplication.instance().palette()



        # Debounce timer: coalesces side-panel rebuilds (folder tree, submitter

        # list, status bar, dashboard) after rapid quick-action clicks so each

        # license/author button press feels snappy.

        self._panels_refresh_timer = QTimer(self)

        self._panels_refresh_timer.setSingleShot(True)

        self._panels_refresh_timer.setInterval(120)

        self._panels_refresh_timer.timeout.connect(self._refresh_side_panels)



        self._build_ui()

        restored_repo = self._restore_settings()

        if repo_root:

            self._start_load(repo_root)

        elif restored_repo:

            self._start_load(restored_repo)



    # ------------------------------------------------------------------

    # UI construction

    # ------------------------------------------------------------------



    def _build_ui(self):

        # ── Toolbar ────────────────────────────────────────────────────

        tb = QToolBar("Main", self)

        tb.setObjectName("mainToolBar")

        tb.setMovable(False)

        self.addToolBar(tb)



        self._repo_label = QLabel("No repository loaded")

        self._repo_label.setFixedHeight(20)

        self._repo_label.setStyleSheet(

            "QLabel { border: 1px solid #445577; border-radius: 9px;"

            " color: #99bbdd; background: #1c2840; padding: 1px 8px; font-size: 8pt; }"

        )

        tb.addWidget(self._repo_label)



        act_open = QAction("Open Repo…", self)

        act_open.setShortcut("Ctrl+O")

        act_open.triggered.connect(self._open_repo)

        tb.addAction(act_open)



        act_refresh = QAction("⟳ Refresh", self)

        act_refresh.setShortcut("F5")

        act_refresh.triggered.connect(self._refresh)

        tb.addAction(act_refresh)



        # ── Filter toolbar (row 2) ─────────────────────────────────────

        self.addToolBarBreak()

        tb2 = QToolBar("Filters", self)

        tb2.setObjectName("filterToolBar")

        tb2.setMovable(False)

        self.addToolBar(tb2)



        tb2.addWidget(QLabel("  Status: "))

        _STATUS_FILTER_DEFS = [

            ("Gap",       "gap",               QColor("#a04444")),

            ("Intent",    "intent",            QColor("#9a7235")),

            ("Awaiting",  "awaiting_license",  QColor("#eb8c1c")),

            ("Confirmed", "confirmed",         QColor("#3b7d56")),

            ("Merged",    "merged",            QColor("#3d6e96")),

        ]

        self._status_btns: list[QPushButton] = []

        for _slbl, _sval, _sclr in _STATUS_FILTER_DEFS:

            _sb = QPushButton(_slbl)

            _sb.setCheckable(True)

            _sb.setFixedHeight(22)

            _scs = _sclr.name()

            _sb.setStyleSheet(

                f"QPushButton {{ border: 1px solid {_scs}; border-radius: 4px;"

                f" color: {_scs}; background: transparent; padding: 1px 5px; font-size: 9pt; }}"

                f" QPushButton:checked {{ background: {_scs}; color: white; font-weight: bold; }}"

                f" QPushButton:hover:!checked {{ background: rgba({_sclr.red()},{_sclr.green()},{_sclr.blue()},55); }}"

            )

            _sb.setProperty("status_val", _sval)

            _sb.toggled.connect(self._apply_filters)

            tb2.addWidget(_sb)

            self._status_btns.append(_sb)



        self._dirty_toggle = QPushButton("⚑ Dirty")

        self._dirty_toggle.setCheckable(True)

        self._dirty_toggle.setFixedHeight(22)

        self._dirty_toggle.setToolTip("Show only records with unsaved changes")

        self._dirty_toggle.setStyleSheet(

            "QPushButton { border: 1px solid #8a6030; border-radius: 4px;"

            " color: #8a6030; background: transparent; padding: 1px 6px; font-size: 9pt; }"

            " QPushButton:checked { background: #8a6030; color: white; font-weight: bold; }"

            " QPushButton:hover:!checked { background: rgba(138,96,48,55); }"

        )

        self._dirty_toggle.toggled.connect(self._apply_filters)

        tb2.addWidget(self._dirty_toggle)



        tb2.addSeparator()



        tb2.addWidget(QLabel("  Search: "))

        self._search_field_combo = QComboBox()

        self._search_field_combo.addItem("File",         "path")

        self._search_field_combo.addItem("Submitter",    "submitter")

        self._search_field_combo.addItem("Contributor",  "contributor")

        self._search_field_combo.addItem("License",      "license")

        self._search_field_combo.addItem("Date",         "date")

        self._search_field_combo.addItem("Last changed", "last_changed")

        self._search_field_combo.addItem("PR #",         "pr")

        self._search_field_combo.addItem("Flags",        "flag")

        self._search_field_combo.addItem("Comments",     "comments")

        self._search_field_combo.currentIndexChanged.connect(self._on_search_field_changed)

        tb2.addWidget(self._search_field_combo)



        # Stacked input: page 0 = text QLineEdit + regex toggle, page 1 = date picker

        self._search_stack = QStackedWidget()

        self._search_stack.setFixedWidth(312)



        _search_page = QWidget()

        _sp_layout = QHBoxLayout(_search_page)

        _sp_layout.setContentsMargins(0, 0, 0, 0)

        _sp_layout.setSpacing(2)

        self._search = QLineEdit()

        self._search.setPlaceholderText("filter by file path…")

        self._search.textChanged.connect(self._on_search_text_changed)

        self._search.setClearButtonEnabled(True)

        _sp_layout.addWidget(self._search)

        self._regex_btn = QPushButton("[.*]")

        self._regex_btn.setCheckable(True)

        self._regex_btn.setFixedSize(32, 22)

        self._regex_btn.setToolTip("Toggle regex mode for text search")

        self._regex_btn.setStyleSheet(

            "QPushButton { border: 1px solid #4d6680; border-radius: 4px;"

            " color: #4d6680; background: transparent; font-size: 8pt; }"

            " QPushButton:checked { background: #2a4a72; color: white; border-color: #3d6096; font-weight: bold; }"

            " QPushButton:hover:!checked { background: rgba(77,102,128,55); }"

        )

        self._regex_btn.toggled.connect(self._on_regex_toggled)

        _sp_layout.addWidget(self._regex_btn)

        self._search_stack.addWidget(_search_page)   # index 0



        self._date_filter_widget = _DateFilterWidget()

        self._date_filter_widget.datesChanged.connect(self._apply_filters)

        self._search_stack.addWidget(self._date_filter_widget)  # index 1



        # License filter toggle buttons — page 2 of the stack

        _lic_page = QWidget()

        _lfb_layout = QHBoxLayout(_lic_page)

        _lfb_layout.setContentsMargins(0, 0, 0, 0)

        _lfb_layout.setSpacing(3)

        _LIC_FILTER_BTNS = [

            ("Any",  None,                      QColor("#5a6a7a")),

            ("CLA",  "LicenseRef-BAR-CLA-1.0",  QColor("#3b7d56")),

            ("CC",   "CC-BY-SA-4.0",             QColor("#7a6030")),

            ("GPL",  "GPL-2.0-or-later",          QColor("#5a4888")),

            ("None", "",                          QColor("#8a3535")),

        ]

        self._lic_filter_btns: list[QPushButton] = []

        for _lbl, _lval, _lclr in _LIC_FILTER_BTNS:

            _lb = QPushButton(_lbl)

            _lb.setCheckable(True)

            _lb.setFixedHeight(22)

            _cs = _lclr.name()

            _lb.setStyleSheet(

                f"QPushButton {{ border: 1px solid {_cs}; border-radius: 4px;"

                f" color: {_cs}; background: transparent; padding: 1px 6px; font-size: 9pt; }}"

                f" QPushButton:checked {{ background: {_cs}; color: white; font-weight: bold; }}"

                f" QPushButton:hover:!checked {{ background: rgba({_lclr.red()},{_lclr.green()},{_lclr.blue()},55); }}"

            )

            _lb.setProperty("lic_val", _lval)

            _lb.toggled.connect(self._on_lic_filter_toggled)

            _lfb_layout.addWidget(_lb)

            self._lic_filter_btns.append(_lb)

        self._lic_filter_btns[0].blockSignals(True)

        self._lic_filter_btns[0].setChecked(True)   # "Any" on by default

        self._lic_filter_btns[0].blockSignals(False)

        self._search_stack.addWidget(_lic_page)  # index 2



        # Flag filter — page 3 of the stack: compact combo box.

        _flag_page = QWidget()

        _ffb_layout = QHBoxLayout(_flag_page)

        _ffb_layout.setContentsMargins(0, 0, 0, 0)

        _ffb_layout.setSpacing(3)

        self._flag_filter_combo = QComboBox()

        self._flag_filter_combo.addItem("Any flag", None)

        self._flag_filter_combo.addItem("(none)", "")

        for _fv, _fl, _fc in FLAG_DEFS:

            if _fv == "":

                continue

            self._flag_filter_combo.addItem(_fl, _fv)

        self._flag_filter_combo.setFixedHeight(22)

        self._flag_filter_combo.setMinimumWidth(160)

        self._flag_filter_combo.currentIndexChanged.connect(

            lambda _i: self._apply_filters()

        )

        _ffb_layout.addWidget(self._flag_filter_combo)

        _ffb_layout.addStretch(1)

        self._search_stack.addWidget(_flag_page)  # index 3



        tb2.addWidget(self._search_stack)



        tb2.addSeparator()

        _btn_mark_all = QPushButton("Mark all filtered…")

        _btn_mark_all.setFixedHeight(22)

        _btn_mark_all.setToolTip(

            "Bulk-edit ALL currently-visible rows (with confirmation)"

        )

        _btn_mark_all.clicked.connect(self._mark_all_filtered)

        tb2.addWidget(_btn_mark_all)



        # ── Menu bar ──────────────────────────────────────────────────

        menubar = self.menuBar()



        # File

        file_menu = menubar.addMenu("&File")

        file_menu.addAction(act_open)

        file_menu.addSeparator()

        act_export_csv = QAction("Export CSV…", self)

        act_export_csv.setShortcut("Ctrl+Shift+E")

        act_export_csv.setToolTip("Export visible rows to CSV (Ctrl+Shift+E)")

        act_export_csv.triggered.connect(self._export_csv)

        file_menu.addAction(act_export_csv)

        act_import_csv = QAction("Import CSV…", self)

        act_import_csv.setToolTip("Import license/contributor data from CSV")

        act_import_csv.triggered.connect(self._import_csv)

        file_menu.addAction(act_import_csv)

        file_menu.addSeparator()

        file_menu.addAction(act_refresh)



        # Edit

        edit_menu = menubar.addMenu("&Edit")

        act_undo = self._undo_stack.createUndoAction(self, "Undo")

        act_undo.setShortcut(QKeySequence.StandardKey.Undo)

        edit_menu.addAction(act_undo)

        act_redo = self._undo_stack.createRedoAction(self, "Redo")

        act_redo.setShortcut(QKeySequence.StandardKey.Redo)

        edit_menu.addAction(act_redo)

        edit_menu.addSeparator()

        act_edit_sel = QAction("Edit Selected…", self)

        act_edit_sel.setShortcut("Ctrl+E")

        act_edit_sel.triggered.connect(self._edit_selected)

        edit_menu.addAction(act_edit_sel)
        act_purchased = QAction("Mark as Purchased\u2026", self)
        act_purchased.setShortcut("Ctrl+Shift+P")
        act_purchased.setToolTip("Record the selected files as store purchases (release-only)")
        act_purchased.triggered.connect(self._mark_purchased)
        edit_menu.addAction(act_purchased)
        act_owned = QAction("Mark as Owned Work\u2026", self)
        act_owned.setToolTip("Record commissioned or in-house files with their rights holder")
        act_owned.triggered.connect(self._mark_owned)
        edit_menu.addAction(act_owned)

        edit_menu.addSeparator()

        act_save = QAction("Save Changes", self)

        act_save.setShortcut("Ctrl+S")

        act_save.triggered.connect(self._save_changes)

        edit_menu.addAction(act_save)

        act_commit = QAction("Commit && Push…", self)

        act_commit.triggered.connect(self._commit_push)

        edit_menu.addAction(act_commit)

        edit_menu.addSeparator()

        act_copy_tsv = QAction("Copy as TSV", self)

        act_copy_tsv.setShortcut("Ctrl+Shift+C")

        act_copy_tsv.setToolTip("Copy selected (or all visible) rows to clipboard as TSV (Ctrl+Shift+C)")

        act_copy_tsv.triggered.connect(self._copy_tsv)

        edit_menu.addAction(act_copy_tsv)

        act_mark_all = QAction("Mark All Filtered…", self)

        act_mark_all.setToolTip("Bulk-edit ALL currently-visible rows")

        act_mark_all.triggered.connect(self._mark_all_filtered)

        edit_menu.addAction(act_mark_all)

        act_flag_wizard = QAction("Flag Wizard…", self)

        act_flag_wizard.setToolTip("Create or remove custom flags (e.g. Envato, Sketchfab)")

        act_flag_wizard.triggered.connect(self._open_flag_wizard)

        edit_menu.addAction(act_flag_wizard)



        # View

        view_menu = menubar.addMenu("&View")

        act_columns = QAction("Columns…", self)

        act_columns.setToolTip("Show/hide and reorder columns")

        act_columns.triggered.connect(self._open_column_dialog)

        view_menu.addAction(act_columns)

        view_menu.addSeparator()

        act_detail = QAction("Detail Panel", self)

        act_detail.setShortcut("Ctrl+D")

        act_detail.triggered.connect(self._toggle_detail_panel)

        view_menu.addAction(act_detail)

        view_menu.addSeparator()

        self._act_compact = QAction("Compact Mode", self)

        self._act_compact.setCheckable(True)

        self._act_compact.setToolTip("Reduce row height for dense view")

        self._act_compact.toggled.connect(self._set_compact_mode)

        view_menu.addAction(self._act_compact)

        view_menu.addSeparator()

        theme_menu = view_menu.addMenu("Theme")

        self._act_dark = QAction("Dark", self)

        self._act_dark.setCheckable(True)

        self._act_light = QAction("Light", self)

        self._act_light.setCheckable(True)

        self._act_dark.triggered.connect(lambda: self._apply_theme(dark=True))

        self._act_light.triggered.connect(lambda: self._apply_theme(dark=False))

        theme_menu.addAction(self._act_dark)

        theme_menu.addAction(self._act_light)



        # Reload app (top-level menu bar item, next to Help)

        act_menu_reload = QAction("↺ Reload app", self)

        act_menu_reload.setShortcut("Ctrl+R")

        act_menu_reload.setToolTip("Restart the process to pick up code changes (Ctrl+R)")

        act_menu_reload.triggered.connect(self._restart_app)

        menubar.addAction(act_menu_reload)



        # Help

        help_menu = menubar.addMenu("&Help")

        act_shortcuts = QAction("Keyboard Shortcuts…", self)

        act_shortcuts.setShortcut("F1")

        act_shortcuts.triggered.connect(self._toggle_help_overlay)

        help_menu.addAction(act_shortcuts)

        act_whatsthis = QAction("What's This?", self)

        act_whatsthis.setShortcut("Shift+F1")

        act_whatsthis.triggered.connect(QWhatsThis.enterWhatsThisMode)

        help_menu.addAction(act_whatsthis)

        act_readme = QAction("README…", self)

        act_readme.triggered.connect(self._show_readme)

        help_menu.addAction(act_readme)



        # ── Central area ───────────────────────────────────────────────

        central = QWidget()

        self.setCentralWidget(central)

        outer = QVBoxLayout(central)

        outer.setContentsMargins(4, 4, 4, 4)

        outer.setSpacing(4)



        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)



        # Left pane — tab widget: "Dirs" (folder tree + submitter) | "Dashboard"

        self._left_tabs = QTabWidget()

        self._left_tabs.setMinimumWidth(200)

        self._left_tabs.setMaximumWidth(320)

        self._left_tabs.setDocumentMode(True)

        self._main_splitter.addWidget(self._left_tabs)



        self._left_splitter = QSplitter(Qt.Orientation.Vertical)



        folder_container = QWidget()

        folder_layout = QVBoxLayout(folder_container)

        folder_layout.setContentsMargins(0, 0, 0, 0)

        folder_layout.setSpacing(2)

        folder_header = QLabel("Art directories")

        folder_header.setStyleSheet("font-weight: bold; padding: 2px 4px;")

        folder_layout.addWidget(folder_header)

        self._folder_list = QTreeWidget()

        self._folder_list.setHeaderHidden(True)

        self._folder_list.setUniformRowHeights(True)

        self._folder_list.setSelectionMode(

            QAbstractItemView.SelectionMode.SingleSelection

        )

        self._folder_list.itemSelectionChanged.connect(self._on_folder_selected)

        folder_layout.addWidget(self._folder_list)

        self._left_splitter.addWidget(folder_container)



        # Submitter browser — multi-select list of all known submitters

        submitter_container = QWidget()

        sub_layout = QVBoxLayout(submitter_container)

        sub_layout.setContentsMargins(0, 0, 0, 0)

        sub_layout.setSpacing(2)

        sub_header = QLabel("Submitters")

        sub_header.setStyleSheet("font-weight: bold; padding: 2px 4px;")

        sub_layout.addWidget(sub_header)

        self._submitter_list = QListWidget()

        self._submitter_list.setSelectionMode(

            QAbstractItemView.SelectionMode.ExtendedSelection

        )

        self._submitter_list.setToolTip(

            "Select one or more submitters to filter the file table.\n"

            "Click 'All' or deselect all to show all submitters.\n"

            "Sorted by gap count — red = has gaps, orange = intent only, green = cleared."

        )

        self._submitter_list.itemSelectionChanged.connect(self._on_submitter_selected)

        sub_layout.addWidget(self._submitter_list)

        # One-click bulk-confirm for selected author(s) — Session 9

        self._confirm_author_btn = QPushButton("Confirm all gaps for author →")

        self._confirm_author_btn.setEnabled(False)

        self._confirm_author_btn.setToolTip(

            "Bulk-confirm all gap/intent files for the selected author(s)\n"

            "using the default license from the toolbar combo."

        )

        self._confirm_author_btn.setWhatsThis(

            "Confirms every gap/intent record belonging to the currently selected\n"

            "author(s) in one click. Uses the bulk-license combo in the toolbar\n"

            "as the license. Fully undoable via Ctrl+Z."

        )

        self._confirm_author_btn.clicked.connect(self._confirm_all_for_author)

        sub_layout.addWidget(self._confirm_author_btn)

        self._left_splitter.addWidget(submitter_container)



        self._left_splitter.setSizes([300, 200])



        # Register left pane tabs

        self._left_tabs.addTab(self._left_splitter, "Dirs")

        self._dashboard = _DashboardPanel()

        self._dashboard.sig_jump_to_dir.connect(self._jump_to_dir)

        self._left_tabs.addTab(self._dashboard, "Dashboard")



        # Right pane — file table

        self._table = QTableView()

        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        self._table.setAlternatingRowColors(True)

        self._table.setSortingEnabled(True)

        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._table.customContextMenuRequested.connect(self._context_menu)



        self._source_model = FileTableModel([])

        self._proxy = FilterProxyModel()

        self._proxy.setSourceModel(self._source_model)

        self._table.setModel(self._proxy)



        hdr = self._table.horizontalHeader()

        hdr.setSectionsMovable(True)

        # All columns interactive (drag-to-resize like Excel)

        for col in range(9):

            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)

        # Default widths  [Status, File, License, Contributor, Submitter, PR, Date, Last Changed, Actions]

        _default_col_widths = [40, 320, 160, 160, 120, 60, 90, 90, ActionDelegate.COL_W + 8, 140]

        for col, w in enumerate(_default_col_widths):

            hdr.resizeSection(col, w)

        hdr.setMinimumSectionSize(40)

        self._table.verticalHeader().setVisible(False)



        # QWhatsThis on major UI sections

        self._table.setWhatsThis(

            "File table — shows all art files and their license status.\n"

            "Select rows and press 1/2/3/4 to quick-assign; double-click License or Contributor to inline-edit.\n"

            "Right-click for more options."

        )

        self._folder_list.setWhatsThis(

            "Art directory list — click a directory to filter the table. "

            "'All art files' shows everything."

        )

        self._submitter_list.setWhatsThis(

            "Submitter browser — click one or more names to filter by that contributor. "

            "Select '(All submitters)' or clear selection to show all."

        )

        self._dirty_toggle.setWhatsThis(

            "Dirty-only filter — when checked, shows only records with unsaved changes.\n"

            "Useful for reviewing what will be written before Save / Commit."

        )

        self._search.setWhatsThis(

            "Text search field — type to filter by the selected field (Submitter, Contributor, License, PR, Date…).\n"

            "Prefix with '#' to auto-switch to PR# mode.  Enable [.*] for regex."

        )

        self._regex_btn.setWhatsThis(

            "[.*] — toggle regular-expression mode for the text search field.\n"

            "Invalid regex patterns are highlighted in red."

        )

        self._dashboard.setWhatsThis(

            "Dashboard tab — per-directory progress bars showing gap/intent/confirmed/merged counts.\n"

            "Click a directory row to jump to it in the file table."

        )



        # ── Action delegate ────────────────────────────────────────────

        self._status_delegate = StatusDotDelegate(self._table)

        self._table.setItemDelegateForColumn(COL_STATUS, self._status_delegate)



        self._action_delegate = ActionDelegate(self._table)

        self._table.setItemDelegateForColumn(COL_ACTIONS, self._action_delegate)

        self._action_delegate.install_on(self._table)

        self._action_delegate.sig_confirm_author.connect(self._quick_confirm_author)

        self._action_delegate.sig_assign_license.connect(self._quick_assign_license)

        self._action_delegate.sig_clear_record.connect(self._quick_clear_record)



        self._status_delegate.set_action_delegate(self._action_delegate)

        self._row_highlight_delegate = RowHighlightDelegate(self._action_delegate, self._table)

        self._table.setItemDelegate(self._row_highlight_delegate)



        # ── Inline edit delegate (License + Contributor columns) ───────

        self._inline_delegate = InlineEditDelegate(

            self._undo_stack,

            lambda: self._source_model,

            self._refresh_after_mutation,

            dir_license_getter=self._get_dir_license,

            dir_license_saver=self._save_dir_license,

            action_delegate=self._action_delegate,

            parent=self._table,

        )

        self._table.setItemDelegateForColumn(COL_LICENSE, self._inline_delegate)

        self._table.setItemDelegateForColumn(COL_CONTRIBUTOR, self._inline_delegate)

        self._table.setItemDelegateForColumn(COL_FLAG, self._inline_delegate)



        # Selectable-text delegate for read-only columns (File, Submitter, PR, Date, Last Changed)

        self._selectable_delegate = SelectableTextDelegate(self._action_delegate, self._table)

        for _col in (COL_FILE, COL_SUBMITTER, COL_PR, COL_DATE, COL_LAST_CHANGED):

            self._table.setItemDelegateForColumn(_col, self._selectable_delegate)



        # Edit triggers: double-click or F2 for editable cols; SelectedClicked opens

        # the read-only QLineEdit in selectable cols (click on already-current cell).

        self._table.setEditTriggers(

            QAbstractItemView.EditTrigger.DoubleClicked |

            QAbstractItemView.EditTrigger.EditKeyPressed |

            QAbstractItemView.EditTrigger.SelectedClicked

        )



        self._main_splitter.addWidget(self._table)

        self._main_splitter.setStretchFactor(0, 0)

        self._main_splitter.setStretchFactor(1, 1)



        # Detail panel (Ctrl+D toggle) — right side panel

        self._detail_panel = _DetailPanel(parent=self)

        self._detail_panel.sig_filter_author.connect(self._filter_by_author)

        self._detail_panel.sig_set_flag.connect(self._set_record_flag)

        self._detail_panel.sig_add_comment.connect(self._add_record_comment)

        self._detail_panel.sig_delete_comment.connect(self._delete_record_comment)

        self._detail_panel.setWhatsThis(

            "Detail panel (Ctrl+D) — shows full path, git author, dates, license text excerpt,\n"

            "PR link, and image thumbnail for the selected row.\n"

            "Click 'Show all files by <author>' to filter by that contributor."

        )

        self._main_splitter.addWidget(self._detail_panel)

        self._main_splitter.setStretchFactor(2, 0)

        self._detail_panel.setVisible(False)



        # ── Filter chips bar (below toolbar, inside central widget) ────

        self._chips_bar = _FilterChipsBar()

        self._chips_bar.chip_clear.connect(self._on_chip_clear)

        outer.addWidget(self._chips_bar)



        outer.addWidget(self._main_splitter, stretch=1)





        # ── Bottom action bar — row 1: bulk actions ────────────────────

        bar = QHBoxLayout()

        bar.setSpacing(4)

        outer.addLayout(bar)



        self._sel_label = QLabel("No selection")

        bar.addWidget(self._sel_label)



        self._bulk_selected_btn = QPushButton("Confirm All Selected")

        self._bulk_selected_btn.setToolTip(

            "Set all selected gap/intent rows to Confirmed.\n"

            "Select the target license in the combo. Only rows with a known author are affected."

        )

        self._bulk_selected_btn.clicked.connect(self._bulk_confirm_selected)

        bar.addWidget(self._bulk_selected_btn)



        self._bulk_all_btn = QPushButton("Confirm All")

        self._bulk_all_btn.setToolTip(

            "Set all visible gap/intent rows to Confirmed.\n"

            "Select the target license in the combo. Only rows with a known author are affected."

        )

        self._bulk_all_btn.clicked.connect(self._bulk_confirm)

        bar.addWidget(self._bulk_all_btn)



        self._bulk_license_combo = QComboBox()

        for label, data, _clr in _LICENSE_BTNS:

            self._bulk_license_combo.addItem(label, data)

        self._bulk_license_combo.setToolTip("License used by Confirm All / Confirm All Selected")

        bar.addWidget(self._bulk_license_combo)



        # ── Bulk action buttons (apply to selection) ───────────────────

        _BULK_ACTIONS = [

            ("Author", QColor("#4888a8"), None),

            ("CC",     QColor("#c07830"), "CC-BY-SA-4.0"),

            ("CLA",    QColor("#3d9b70"), "LicenseRef-BAR-CLA-1.0"),

            ("GPL",    QColor("#7a60b0"), "GPL-2.0-or-later"),

            ("MIT",    QColor("#3fa0c4"), "MIT"),

            ("CC0",    QColor("#9cae3a"), "CC0-1.0"),

            ("Clear",  QColor("#b84848"), None),

        ]

        for _label, _color, _lic in _BULK_ACTIONS:

            _btn = QPushButton(_label)

            _btn.setFixedWidth(52)

            _clr_str = _color.name()

            _btn.setStyleSheet(

                f"QPushButton {{ border: 1px solid {_clr_str}; border-radius: 4px;"

                f" color: {_clr_str}; background: transparent; padding: 2px 4px; }}"

                f" QPushButton:hover {{ background: {_clr_str}; color: white; }}"

            )

            if _label == "Author":

                _btn.setToolTip("Confirm author on all selected rows")

                _btn.clicked.connect(self._bulk_author_selected)

            elif _label == "Clear":

                _btn.setToolTip("Reset all selected rows to Gap")

                _btn.clicked.connect(self._bulk_clear_selected)

            else:

                _btn.setToolTip(f"Assign {_lic} to all selected rows")

                _btn.clicked.connect(lambda checked, l=_lic: self._bulk_license_selected(l))

            bar.addWidget(_btn)

        # ── Bulk flag (apply to selection) ──────────────────────────────

        self._bulk_flag_combo = QComboBox()

        for _fv, _fl, _fc in FLAG_DEFS:

            self._bulk_flag_combo.addItem(_fl, _fv)

            self._bulk_flag_combo.setItemData(self._bulk_flag_combo.count() - 1, QColor(_fc),

                                              Qt.ItemDataRole.ForegroundRole)

        self._bulk_flag_combo.setToolTip("Flag added by the Flag button to all selected rows")

        bar.addWidget(self._bulk_flag_combo)

        _flag_btn = QPushButton("Flag")

        _flag_btn.setFixedWidth(52)

        _flag_clr = "#5865f2"

        _flag_btn.setStyleSheet(

            f"QPushButton {{ border: 1px solid {_flag_clr}; border-radius: 4px;"

            f" color: {_flag_clr}; background: transparent; padding: 2px 4px; }}"

            f" QPushButton:hover {{ background: {_flag_clr}; color: white; }}"

        )

        _flag_btn.setToolTip("Add the chosen flag to all selected rows ((none) clears all flags)")

        _flag_btn.clicked.connect(self._bulk_flag_selected)

        bar.addWidget(_flag_btn)



        bar.addStretch()



        self._edit_btn = QPushButton("Edit Selected…")

        self._edit_btn.setFixedHeight(36)

        self._edit_btn.setEnabled(False)

        self._edit_btn.clicked.connect(self._edit_selected)

        bar.addWidget(self._edit_btn)



        self._save_btn = QPushButton("Save Changes")

        self._save_btn.setFixedHeight(36)

        self._save_btn.setEnabled(False)

        self._save_btn.clicked.connect(self._save_changes)

        bar.addWidget(self._save_btn)



        self._commit_btn = QPushButton("Commit && Push…")

        self._commit_btn.setFixedHeight(36)

        self._commit_btn.setEnabled(False)

        self._commit_btn.clicked.connect(self._commit_push)

        bar.addWidget(self._commit_btn)



        # ── Status bar ─────────────────────────────────────────────────

        self._sb_label = QLabel()

        self.statusBar().addWidget(self._sb_label)



        self._progress_bar = QProgressBar()

        self._progress_bar.setRange(0, 100)

        self._progress_bar.setFixedWidth(200)

        self._progress_bar.setTextVisible(True)

        self._progress_bar.setVisible(False)

        self.statusBar().addPermanentWidget(self._progress_bar)

        self.statusBar().setStyleSheet("QStatusBar::item { border: none; }")



        # Track selection changes

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)



        # ── Keyboard shortcuts — table-focused keys ────────────────────

        # Install on the table widget (key events go to QTableView, not viewport)

        self._table.installEventFilter(self)



        # F1 works from anywhere in the window

        sc_f1 = QShortcut(QKeySequence("F1"), self)

        sc_f1.activated.connect(self._toggle_help_overlay)



        # Ctrl+L — re-apply last bulk edit (works from anywhere in the window)

        sc_ctrl_l = QShortcut(QKeySequence("Ctrl+L"), self)

        sc_ctrl_l.activated.connect(self._apply_last_edit)



        sc_ctrl_d = QShortcut(QKeySequence("Ctrl+D"), self)

        sc_ctrl_d.activated.connect(self._toggle_detail_panel)



        sc_copy_tsv = QShortcut(QKeySequence("Ctrl+Shift+C"), self)

        sc_copy_tsv.activated.connect(self._copy_tsv)



        sc_copy_cell = QShortcut(QKeySequence("Ctrl+C"), self._table)

        sc_copy_cell.activated.connect(self._copy_current_cell)



        # Explicit undo/redo shortcuts — QAction shortcuts added to the Edit

        # menu are not reliably fired in PyQt6 when QTableView has focus, so

        # mirror them here with the same QShortcut pattern used for F1/Ctrl+L.

        sc_undo = QShortcut(QKeySequence.StandardKey.Undo, self)

        sc_undo.activated.connect(self._undo_stack.undo)

        sc_redo = QShortcut(QKeySequence.StandardKey.Redo, self)

        sc_redo.activated.connect(self._undo_stack.redo)



        # ── Help overlay (child of MainWindow, covers full client area) ─

        self._help_overlay = _HelpOverlay(self)

        self._help_overlay.setGeometry(self.rect())



        # ── Tab order ─────────────────────────────────────────────────

        QWidget.setTabOrder(self._dirty_toggle, self._search_field_combo)

        QWidget.setTabOrder(self._search_field_combo, self._search)

        QWidget.setTabOrder(self._search, self._folder_list)

        QWidget.setTabOrder(self._folder_list, self._submitter_list)

        QWidget.setTabOrder(self._submitter_list, self._table)

        QWidget.setTabOrder(self._table, self._edit_btn)

        QWidget.setTabOrder(self._edit_btn, self._save_btn)

        QWidget.setTabOrder(self._save_btn, self._commit_btn)



    # ------------------------------------------------------------------

    # Lifecycle — settings + close guard

    # ------------------------------------------------------------------



    def resizeEvent(self, event) -> None:

        super().resizeEvent(event)

        if hasattr(self, "_help_overlay"):

            self._help_overlay.setGeometry(self.rect())



    def eventFilter(self, obj, event) -> bool:

        """Route table key presses to keyboard shortcut handlers."""

        if obj is self._table and event.type() == QEvent.Type.KeyPress:

            key = event.key()

            mods = event.modifiers()

            no_mods = mods == Qt.KeyboardModifier.NoModifier

            shift_only = mods == Qt.KeyboardModifier.ShiftModifier



            if key == Qt.Key.Key_1 and no_mods:

                self._key_confirm_author()

                return True

            if key == Qt.Key.Key_2 and no_mods:

                self._key_assign_license("LicenseRef-BAR-CLA-1.0")

                return True

            if key == Qt.Key.Key_3 and no_mods:

                self._key_assign_license("CC-BY-SA-4.0")

                return True

            if key == Qt.Key.Key_4 and no_mods:

                self._key_assign_license("GPL-2.0-or-later")

                return True

            if key == Qt.Key.Key_N and no_mods:

                self._nav_next_gap(forward=True)

                return True

            if key == Qt.Key.Key_N and shift_only:

                self._nav_next_gap(forward=False)

                return True

            if key in (Qt.Key.Key_F1, Qt.Key.Key_Question):

                self._toggle_help_overlay()

                return True

        return False



    def closeEvent(self, event) -> None:

        if getattr(self, "_restarting", False):

            self._save_settings()

            super().closeEvent(event)

            return

        dirty = [r for r in self._records if r.dirty]

        if dirty:

            ans = QMessageBox.question(

                self,

                "Unsaved changes",

                f"You have {len(dirty)} unsaved change(s). Save before closing?",

                QMessageBox.StandardButton.Save

                | QMessageBox.StandardButton.Discard

                | QMessageBox.StandardButton.Cancel,

                QMessageBox.StandardButton.Save,

            )

            if ans == QMessageBox.StandardButton.Cancel:

                event.ignore()

                return

            if ans == QMessageBox.StandardButton.Save:

                self._save_changes()

                if any(r.dirty for r in self._records):

                    # Save aborted (validation failure) — keep window open

                    event.ignore()

                    return

        self._save_settings()

        super().closeEvent(event)



    def _save_settings(self) -> None:

        s = self._settings

        s.setValue("geometry", self.saveGeometry())

        # windowState intentionally not saved (toolbar layout managed in code).

        s.setValue("mainSplitter", self._main_splitter.saveState())

        s.setValue("leftSplitter", self._left_splitter.saveState())

        s.setValue("detailPanelVisible", self._detail_panel.isVisible())

        if self._repo_root:

            s.setValue("lastRepo", str(self._repo_root))

        checked_statuses = [btn.property("status_val") for btn in self._status_btns if btn.isChecked()]

        s.setValue("filterStatuses", checked_statuses)

        s.setValue("filterDirtyOnly", self._dirty_toggle.isChecked())

        s.setValue("filterField", self._search_field_combo.currentIndex())

        s.setValue("leftTabIndex", self._left_tabs.currentIndex())

        # Column state: order + visibility + widths (replaces separate colWidths)

        s.setValue("headerState", self._table.horizontalHeader().saveState())

        s.setValue("darkMode", self._dark_mode)

        s.setValue("compactMode", self._compact_mode)



    def _restore_settings(self) -> "Path | None":

        s = self._settings

        if geom := s.value("geometry"):

            self.restoreGeometry(geom)

        # NOTE: windowState is intentionally not restored — it persists toolbar

        # layout which would override the two-row toolbar break.

        if sizes := s.value("mainSplitter"):

            self._main_splitter.restoreState(sizes)

        if sizes := s.value("leftSplitter"):

            self._left_splitter.restoreState(sizes)

        detail_vis = s.value("detailPanelVisible", False)

        if isinstance(detail_vis, str):

            detail_vis = detail_vis.lower() == "true"

        self._detail_panel.setVisible(bool(detail_vis))

        if saved_statuses := s.value("filterStatuses"):

            for btn in self._status_btns:

                btn.blockSignals(True)

                btn.setChecked(btn.property("status_val") in (saved_statuses or []))

                btn.blockSignals(False)

        _dirty_saved = s.value("filterDirtyOnly", False)

        if isinstance(_dirty_saved, str):

            _dirty_saved = _dirty_saved.lower() == "true"

        self._dirty_toggle.blockSignals(True)

        self._dirty_toggle.setChecked(bool(_dirty_saved))

        self._dirty_toggle.blockSignals(False)

        if (field_idx := s.value("filterField")) is not None:

            try:

                self._search_field_combo.setCurrentIndex(int(field_idx))

            except (TypeError, ValueError):

                pass

        if col_widths := s.value("colWidths"):

            hdr = self._table.horizontalHeader()

            try:

                for col, w in enumerate(col_widths):

                    hdr.resizeSection(col, int(w))

            except (TypeError, ValueError):

                pass

        if (tab_idx := s.value("leftTabIndex")) is not None:

            try:

                self._left_tabs.setCurrentIndex(int(tab_idx))

            except (TypeError, ValueError):

                pass

        # Column header state: order + visibility + widths

        if header_state := s.value("headerState"):

            try:

                self._table.horizontalHeader().restoreState(header_state)

            except Exception:

                pass

        # Fallback: legacy colWidths (migrate from older sessions)

        elif col_widths := s.value("colWidths"):

            hdr = self._table.horizontalHeader()

            try:

                for col, w in enumerate(col_widths):

                    hdr.resizeSection(col, int(w))

            except (TypeError, ValueError):

                pass

        # A saved header state may predate newly-added quick-action buttons;

        # make sure the Actions column is wide enough to show all of them.

        _hdr = self._table.horizontalHeader()

        _min_actions_w = ActionDelegate.COL_W + 8

        if _hdr.sectionSize(COL_ACTIONS) < _min_actions_w:

            _hdr.resizeSection(COL_ACTIONS, _min_actions_w)

        # Dark mode

        dark_val = s.value("darkMode", False)

        if isinstance(dark_val, str):

            dark_val = dark_val.lower() == "true"

        if dark_val:

            self._apply_theme(dark=True, _save=False)

        else:

            self._act_light.setChecked(True)

        # Compact mode

        compact_val = s.value("compactMode", False)

        if isinstance(compact_val, str):

            compact_val = compact_val.lower() == "true"

        if compact_val:

            self._act_compact.setChecked(True)  # triggers _set_compact_mode via toggled signal

        last_repo = s.value("lastRepo")

        return Path(last_repo) if last_repo else None



    # ------------------------------------------------------------------

    # Loading

    # ------------------------------------------------------------------



    def _open_repo(self):

        path = QFileDialog.getExistingDirectory(self, "Select BAR repository root")

        if path:

            self._start_load(Path(path))



    def _refresh(self):

        if self._repo_root:

            self._start_load(self._repo_root)



    def _restart_app(self):

        """Relaunch the process to pick up any .py changes on disk."""

        import subprocess, sys

        dirty = [r for r in self._records if r.dirty]

        if dirty:

            ans = QMessageBox.question(

                self,

                "Unsaved changes",

                f"You have {len(dirty)} unsaved change(s). Save before reloading?",

                QMessageBox.StandardButton.Save

                | QMessageBox.StandardButton.Discard

                | QMessageBox.StandardButton.Cancel,

                QMessageBox.StandardButton.Save,

            )

            if ans == QMessageBox.StandardButton.Cancel:

                return

            if ans == QMessageBox.StandardButton.Save:

                self._save_changes()

        args = [sys.executable, "-m", "license_auditor"]

        if self._repo_root:

            args.append(str(self._repo_root))

        cwd = Path(__file__).parent.parent

        kwargs = {"cwd": str(cwd)}

        if sys.platform == "win32":

            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

        try:

            subprocess.Popen(args, **kwargs)

        except Exception as exc:

            QMessageBox.critical(self, "Reload failed", f"Could not launch new instance:\n{exc}")

            return

        for r in self._records:

            r.dirty = False

        self._restarting = True

        self.close()



    def _start_load(self, root: Path):

        self._repo_root = root

        self._repo_label.setText(str(root))

        self._folder_list.clear()

        self._source_model.refresh([])



        self._progress_bar.setValue(0)

        self._progress_bar.setVisible(True)

        self._loader = _LoadThread(root)

        self._loader.done.connect(self._on_load_done)

        self._loader.error.connect(self._on_load_error)

        self._loader.progress.connect(self._on_load_progress)

        self._loader.start()



    def _on_load_done(self, records: list[FileRecord], raw_sigs: list[dict], raw_assigns: list[dict], raw_flags_comments: dict | None = None):

        self._progress_bar.setVisible(False)

        self._records = records

        self._raw_sigs = raw_sigs

        self._raw_assigns = raw_assigns

        self._raw_flags_comments = raw_flags_comments or {}

        self._source_model.refresh(records)

        self._rebuild_folder_tree(records)

        self._rebuild_submitter_list(records)

        self._update_status_bar()

        self._detail_panel.set_repo_root(self._repo_root)



        # Dashboard: gap delta vs. last audit + initial refresh

        cur_gaps = sum(1 for r in records if r.status == "gap")

        prev_gaps = self._settings.value("lastAuditGaps")

        try:

            new_gaps_delta = cur_gaps - int(prev_gaps) if prev_gaps is not None else None

        except (TypeError, ValueError):

            new_gaps_delta = None

        self._settings.setValue("lastAuditGaps", cur_gaps)

        self._load_confirmed = sum(1 for r in records if r.status == "confirmed")

        self._session_confirmed = 0

        self._dashboard.refresh(records, 0, new_gaps_delta)



    def _on_load_progress(self, percent: int, label: str):

        self._progress_bar.setValue(percent)

        self._sb_label.setText(label)



    def _on_load_error(self, msg: str):

        self._progress_bar.setVisible(False)

        QMessageBox.critical(self, "Load error", msg)

        self._sb_label.setText("Load failed.")



    # ------------------------------------------------------------------

    # Folder tree

    # ------------------------------------------------------------------



    def _rebuild_folder_tree(self, records: list[FileRecord]):

        # Preserve current selection + expanded prefixes

        sel_items = self._folder_list.selectedItems()

        prev_prefix = sel_items[0].data(0, Qt.ItemDataRole.UserRole) if sel_items else ""

        expanded: set[str] = set()



        def _collect_expanded(item: QTreeWidgetItem):

            if item.isExpanded():

                p = item.data(0, Qt.ItemDataRole.UserRole)

                if p is not None:

                    expanded.add(p)

            for i in range(item.childCount()):

                _collect_expanded(item.child(i))



        for i in range(self._folder_list.topLevelItemCount()):

            _collect_expanded(self._folder_list.topLevelItem(i))



        self._folder_list.blockSignals(True)

        self._folder_list.clear()



        from collections import defaultdict

        # counts[prefix][status] = n   ;   children[prefix] = set of immediate child prefixes

        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        children: dict[str, set[str]] = defaultdict(set)

        for r in records:

            parts = r.path.split("/")

            if len(parts) < 2:

                continue

            # Walk every directory ancestor: parts[:1], parts[:2], ..., parts[:-1]

            for i in range(1, len(parts)):

                prefix = "/".join(parts[:i])

                counts[prefix][r.status] += 1

                if i + 1 < len(parts):

                    children[prefix].add("/".join(parts[: i + 1]))



        total = len(records)

        total_gaps = sum(1 for r in records if r.status == "gap")



        root_item = QTreeWidgetItem(

            [f"All art files  ({total} files, {total_gaps} gaps)"]

        )

        root_item.setData(0, Qt.ItemDataRole.UserRole, "")  # '' = show all

        if total_gaps:

            root_item.setForeground(0, QColor("#e74c3c"))

        self._folder_list.addTopLevelItem(root_item)



        def _make_item(prefix: str) -> QTreeWidgetItem:

            c = counts[prefix]

            n = sum(c.values())

            gaps = c.get("gap", 0)

            intent = c.get("intent", 0)

            # Nested top-level art dirs (e.g. "luaui/images") keep their full
            # path so they don't collide with same-named folders elsewhere
            # ("luaui/images" vs "luarules/images" both ending in "images").
            if prefix in ART_TOP_DIRS and "/" in prefix:
                name = prefix
            else:
                name = prefix.rsplit("/", 1)[-1]

            parts_label = [f"{n} files"]

            if gaps:

                parts_label.append(f"{gaps} gaps")

            if intent:

                parts_label.append(f"{intent} intent")

            item = QTreeWidgetItem([f"{name}/  ({', '.join(parts_label)})"])

            item.setData(0, Qt.ItemDataRole.UserRole, prefix)

            if gaps:

                item.setForeground(0, QColor("#e74c3c"))

            elif intent:

                item.setForeground(0, QColor("#e67e22"))

            else:

                item.setForeground(0, QColor("#27ae60"))

            for ch in sorted(children[prefix]):

                item.addChild(_make_item(ch))

            return item



        for top in ART_TOP_DIRS:

            if top not in counts:

                continue

            self._folder_list.addTopLevelItem(_make_item(top))



        # Restore expanded state. By default, keep all collapsed except previously

        # expanded ones (and always expand the synthetic root).

        root_item.setExpanded(True)



        def _restore(item: QTreeWidgetItem):

            p = item.data(0, Qt.ItemDataRole.UserRole)

            if p and p in expanded:

                item.setExpanded(True)

            for i in range(item.childCount()):

                _restore(item.child(i))



        for i in range(self._folder_list.topLevelItemCount()):

            _restore(self._folder_list.topLevelItem(i))



        # Restore previous selection (no signal → no filter re-trigger)

        restored = False

        if prev_prefix:

            def _find(item: QTreeWidgetItem) -> QTreeWidgetItem | None:

                if item.data(0, Qt.ItemDataRole.UserRole) == prev_prefix:

                    return item

                for i in range(item.childCount()):

                    f = _find(item.child(i))

                    if f is not None:

                        return f

                return None



            for i in range(self._folder_list.topLevelItemCount()):

                f = _find(self._folder_list.topLevelItem(i))

                if f is not None:

                    # Expand all ancestors so the selected item is visible

                    parent = f.parent()

                    while parent is not None:

                        parent.setExpanded(True)

                        parent = parent.parent()

                    self._folder_list.setCurrentItem(f)

                    restored = True

                    break

        if not restored:

            self._folder_list.setCurrentItem(root_item)

        self._folder_list.blockSignals(False)



    def _on_folder_selected(self):

        items = self._folder_list.selectedItems()

        if not items:

            return

        prefix = items[0].data(0, Qt.ItemDataRole.UserRole)

        self._proxy.set_top_dir(prefix or "")

        self._update_status_bar()



    # ------------------------------------------------------------------

    # Submitter list

    # ------------------------------------------------------------------



    def _rebuild_submitter_list(self, records: list[FileRecord]) -> None:

        # Preserve current selection by name (UserRole)

        prev_selected: set[str] = {

            item.data(Qt.ItemDataRole.UserRole)

            for item in self._submitter_list.selectedItems()

            if item.data(Qt.ItemDataRole.UserRole) is not None

        }



        self._submitter_list.blockSignals(True)

        self._submitter_list.clear()

        all_item = QListWidgetItem("(All submitters)")

        all_item.setData(Qt.ItemDataRole.UserRole, None)

        self._submitter_list.addItem(all_item)



        # Compute per-author status counts

        from collections import defaultdict

        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for r in records:

            name = r.proxy_submitter or r.git_author

            if name:

                counts[name][r.status] += 1



        # Sort: most gaps first, then alphabetical

        sorted_authors = sorted(

            counts.items(),

            key=lambda kv: (-kv[1].get("gap", 0), kv[0].lower()),

        )



        for name, c in sorted_authors:

            total = sum(c.values())

            gaps = c.get("gap", 0)

            intent = c.get("intent", 0)

            parts = [f"{total}"]

            if gaps:

                parts.append(f"{gaps} gap{'s' if gaps != 1 else ''}")

            if intent:

                parts.append(f"{intent} intent")

            label = f"{name}  ({', '.join(parts)})"

            item = QListWidgetItem(label)

            item.setData(Qt.ItemDataRole.UserRole, name)

            if gaps:

                item.setForeground(QColor("#e74c3c"))

            elif intent:

                item.setForeground(QColor("#e67e22"))

            else:

                item.setForeground(QColor("#27ae60"))

            self._submitter_list.addItem(item)



        # Restore previous selection

        if prev_selected:

            for i in range(self._submitter_list.count()):

                it = self._submitter_list.item(i)

                if it.data(Qt.ItemDataRole.UserRole) in prev_selected:

                    it.setSelected(True)

        else:

            self._submitter_list.item(0).setSelected(True)



        self._submitter_list.blockSignals(False)

        # Update confirm-button state without re-triggering filter

        self._update_confirm_author_btn()



    def _on_submitter_selected(self) -> None:

        selected_items = self._submitter_list.selectedItems()

        selected: set[str] = set()

        has_all = False

        for item in selected_items:

            name = item.data(Qt.ItemDataRole.UserRole)

            if name is None:  # "(All submitters)" sentinel

                has_all = True

                break

            selected.add(name)

        # "(All submitters)" or nothing selected → show all

        if not selected_items or has_all:

            self._proxy.set_submitters(set())

        else:

            self._proxy.set_submitters(selected)

        self._update_confirm_author_btn()

        self._update_status_bar()



    def _update_confirm_author_btn(self) -> None:

        """Enable the confirm-all button only when specific author(s) are selected."""

        names = {

            item.data(Qt.ItemDataRole.UserRole)

            for item in self._submitter_list.selectedItems()

            if item.data(Qt.ItemDataRole.UserRole) is not None

        }

        self._confirm_author_btn.setEnabled(bool(names))



    def _confirm_all_for_author(self) -> None:

        """Session 9: bulk-confirm all gap/intent files for the selected author(s)."""

        selected_names = {

            item.data(Qt.ItemDataRole.UserRole)

            for item in self._submitter_list.selectedItems()

            if item.data(Qt.ItemDataRole.UserRole) is not None

        }

        if not selected_names:

            return

        eligible = [

            r for r in self._records

            if (r.proxy_submitter or r.git_author) in selected_names

            and r.status in ("gap", "intent")

        ]

        if not eligible:

            self._flash_status("No gap/intent records for selected author(s).")

            return

        lic = self._bulk_license_combo.currentData()

        names_str = ", ".join(sorted(selected_names))

        reply = QMessageBox.question(

            self, "Confirm all for author",

            f"Confirm {len(eligible)} gap/intent file(s) for:\n{names_str}\n\n"

            f"License: {lic}\n\nProceed?",

            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,

            QMessageBox.StandardButton.Yes,

        )

        if reply != QMessageBox.StandardButton.Yes:

            return

        befores = [_RecordSnapshot.capture(r) for r in eligible]

        for rec in eligible:

            author = rec.proxy_submitter or rec.git_author or ""

            rec.contributor = contributor_for_license(rec, lic, author)

            rec.license = lic

            rec.status = "confirmed"

            rec.dirty = True

        afters = [_RecordSnapshot.capture(r) for r in eligible]

        self._undo_stack.push(_RecordEditCommand(

            eligible, befores, afters, self._source_model,

            self._refresh_after_mutation,

            f"Confirm all for author ({len(eligible)})",

        ))

        self._flash_status(f"Confirmed {len(eligible)} record(s) for {names_str}.")



    # ------------------------------------------------------------------

    # Filtering

    # ------------------------------------------------------------------



    _SEARCH_FIELD_PLACEHOLDER = {

        "path":         "filter by file path…",

        "submitter":    "filter by submitter…",

        "contributor":  "filter by contributor…",

        "license":      "filter by license…",

        "date":         "filter by license date…",

        "last_changed": "filter by last-changed date…",

        "pr":           "filter by PR # (e.g. 1234)…",

        "flag":         "filter by flag…",

        "comments":     "filter by comment text…",

    }



    def _on_search_field_changed(self) -> None:

        field = self._search_field_combo.currentData()

        if field == "license":

            self._search_stack.setCurrentIndex(2)

            self._search.blockSignals(True)

            self._search.clear()

            self._search.blockSignals(False)

            self._date_filter_widget.clear()

            self._reset_flag_filter_btns()

        elif field in ("date", "last_changed"):

            self._search_stack.setCurrentIndex(1)

            self._search.blockSignals(True)

            self._search.clear()

            self._search.blockSignals(False)

            self._reset_lic_filter_btns()

            self._reset_flag_filter_btns()

        elif field == "flag":

            self._search_stack.setCurrentIndex(3)

            self._search.blockSignals(True)

            self._search.clear()

            self._search.blockSignals(False)

            self._date_filter_widget.clear()

            self._reset_lic_filter_btns()

        else:

            self._search_stack.setCurrentIndex(0)

            self._search.setPlaceholderText(self._SEARCH_FIELD_PLACEHOLDER.get(field, "filter\u2026"))

            self._date_filter_widget.clear()

            self._reset_lic_filter_btns()

            self._reset_flag_filter_btns()

        self._proxy.set_text_field(field)

        self._apply_filters()



    def _reset_lic_filter_btns(self) -> None:

        """Restore 'Any' as the only selected license button without triggering filters."""

        for btn in self._lic_filter_btns:

            btn.blockSignals(True)

            btn.setChecked(False)

            btn.blockSignals(False)

        self._lic_filter_btns[0].blockSignals(True)

        self._lic_filter_btns[0].setChecked(True)

        self._lic_filter_btns[0].blockSignals(False)

        self._proxy.set_license_filter(set())



    def _reset_flag_filter_btns(self) -> None:

        if hasattr(self, "_flag_filter_combo"):

            self._flag_filter_combo.blockSignals(True)

            self._flag_filter_combo.setCurrentIndex(0)

            self._flag_filter_combo.blockSignals(False)

        self._proxy.set_flag_filter(set())



    def _on_flag_filter_toggled(self, checked: bool) -> None:

        # Legacy chip handler — combo is wired directly to _apply_filters now.

        self._apply_filters()



    def _on_lic_filter_toggled(self, checked: bool) -> None:

        any_btn = self._lic_filter_btns[0]

        sender = self.sender()

        if sender is any_btn:

            if checked:

                for btn in self._lic_filter_btns[1:]:

                    btn.blockSignals(True)

                    btn.setChecked(False)

                    btn.blockSignals(False)

            else:

                # Prevent unchecking Any when it's the only active button

                if not any(btn.isChecked() for btn in self._lic_filter_btns[1:]):

                    any_btn.blockSignals(True)

                    any_btn.setChecked(True)

                    any_btn.blockSignals(False)

        else:

            if checked:

                any_btn.blockSignals(True)

                any_btn.setChecked(False)

                any_btn.blockSignals(False)

            else:

                if not any(btn.isChecked() for btn in self._lic_filter_btns[1:]):

                    any_btn.blockSignals(True)

                    any_btn.setChecked(True)

                    any_btn.blockSignals(False)

        self._apply_filters()



    def _apply_filters(self):

        checked_statuses = {btn.property("status_val") for btn in self._status_btns if btn.isChecked()}

        self._proxy.set_status(checked_statuses)

        self._proxy.set_dirty_only(self._dirty_toggle.isChecked())

        self._proxy.set_use_regex(self._regex_btn.isChecked())

        field = self._search_field_combo.currentData()

        self._proxy.set_text_field(field)

        if field == "license":

            active = {

                btn.property("lic_val")

                for btn in self._lic_filter_btns[1:]

                if btn.isChecked()

            }

            self._proxy.set_license_filter(active)

            self._proxy.set_date_filter(set())

            self._proxy.set_flag_filter(set())

            self._proxy.set_text("")

        elif field in ("date", "last_changed"):

            self._proxy.set_license_filter(set())

            self._proxy.set_date_filter(self._date_filter_widget.selected_date_strings())

            self._proxy.set_flag_filter(set())

            self._proxy.set_text("")

        elif field == "flag":

            data = self._flag_filter_combo.currentData() if hasattr(self, "_flag_filter_combo") else None

            active: set = set() if data is None else {data}

            self._proxy.set_flag_filter(active)

            self._proxy.set_license_filter(set())

            self._proxy.set_date_filter(set())

            self._proxy.set_text("")

        else:

            self._proxy.set_license_filter(set())

            self._proxy.set_date_filter(set())

            self._proxy.set_flag_filter(set())

            self._proxy.set_text(self._search.text())

        self._update_status_bar()



    # ------------------------------------------------------------------

    # Session 6: text search helpers (auto-PR, regex toggle)

    # ------------------------------------------------------------------



    def _on_search_text_changed(self, text: str) -> None:

        """Handle search field text changes — auto-switch to PR mode on '#' prefix."""

        # Auto-switch to PR mode when user types '#123'

        if text.startswith("#") and self._search_field_combo.currentData() != "pr":

            idx = self._search_field_combo.findData("pr")

            if idx >= 0:

                self._search.blockSignals(True)

                self._search.setText(text[1:])   # strip the '#'

                self._search.blockSignals(False)

                self._search_field_combo.setCurrentIndex(idx)

                # _on_search_field_changed → _apply_filters handled via signal

                return

        # Regex validation feedback

        if self._regex_btn.isChecked() and text:

            import re as _re

            try:

                _re.compile(text)

                self._search.setStyleSheet("")

                self._search.setToolTip("")

            except _re.error as e:

                self._search.setStyleSheet("background: #5a1a1a;")

                self._search.setToolTip(f"Invalid regex: {e}")

        else:

            self._search.setStyleSheet("")

            self._search.setToolTip("")

        self._apply_filters()



    def _on_regex_toggled(self, checked: bool) -> None:

        """[.*] button — validate regex and re-apply filter."""

        text = self._search.text()

        if checked and text:

            import re as _re

            try:

                _re.compile(text)

                self._search.setStyleSheet("")

                self._search.setToolTip("")

            except _re.error as e:

                self._search.setStyleSheet("background: #5a1a1a;")

                self._search.setToolTip(f"Invalid regex: {e}")

        else:

            self._search.setStyleSheet("")

            self._search.setToolTip("")

        self._apply_filters()



    # ------------------------------------------------------------------

    # Session 6: filter chips bar

    # ------------------------------------------------------------------



    def _compute_chips(self) -> list[tuple[str, str]]:

        """Return (key, label) for each active non-default filter."""

        chips: list[tuple[str, str]] = []

        # Status — only chip when a specific subset is checked

        checked_lbls = [btn.text() for btn in self._status_btns if btn.isChecked()]

        if checked_lbls:

            chips.append(("status", "Status: " + ", ".join(checked_lbls)))

        # Dirty only

        if self._dirty_toggle.isChecked():

            chips.append(("dirty", "Dirty only"))

        # Directory

        top_dir = self._proxy._top_dir_filter

        if top_dir:

            chips.append(("top_dir", f"Dir: {top_dir}/"))

        # Search / license / date

        field = self._search_field_combo.currentData()

        if field == "license":

            active_lics = [btn.text() for btn in self._lic_filter_btns[1:] if btn.isChecked()]

            if active_lics:

                chips.append(("license", "License: " + ", ".join(active_lics)))

        elif field in ("date", "last_changed"):

            n = len(self._date_filter_widget.selected_date_strings())

            if n:

                chips.append(("date", f"Date: {n} date{'s' if n != 1 else ''}"))

        else:

            text = self._search.text().strip()

            if text:

                field_lbl = self._search_field_combo.currentText()

                trimmed = text[:24] + ("\u2026" if len(text) > 24 else "")

                regex_pfx = "[.*] " if self._regex_btn.isChecked() else ""

                chips.append(("search", f"{regex_pfx}{field_lbl}: {trimmed}"))

        # Submitters

        subs = self._proxy._submitter_filter

        if subs:

            sub_list = ", ".join(sorted(subs)[:3])

            suffix = "\u2026" if len(subs) > 3 else ""

            chips.append(("submitter", f"Submitter: {sub_list}{suffix}"))

        return chips



    def _rebuild_chips(self) -> None:

        """Refresh the filter chips bar from current filter state."""

        self._chips_bar.update_chips(self._compute_chips())



    def _on_chip_clear(self, key: str) -> None:

        """Clear a specific filter when the user clicks ✕ on a chip."""

        if key == "status":

            for btn in self._status_btns:

                btn.blockSignals(True)

                btn.setChecked(False)

                btn.blockSignals(False)

            self._apply_filters()

        elif key == "dirty":

            self._dirty_toggle.setChecked(False)   # toggled signal → _apply_filters

        elif key == "top_dir":

            if self._folder_list.count() > 0:

                self._folder_list.setCurrentItem(self._folder_list.item(0))

        elif key == "search":

            self._search.clear()                   # textChanged → _apply_filters

        elif key == "license":

            self._reset_lic_filter_btns()

            self._apply_filters()

        elif key == "date":

            self._date_filter_widget.clear()        # datesChanged → _apply_filters

        elif key == "submitter":

            self._submitter_list.clearSelection()   # selectionChanged → _apply_filters



    # ------------------------------------------------------------------

    # Quick-action button handlers (per-row delegate buttons)

    # ------------------------------------------------------------------



    def _refresh_after_mutation(self, records: list | None = None) -> None:

        """UI refresh after an undoable record mutation.



        Fast path: when *records* is a small list of mutated records, emits

        per-row ``dataChanged`` on the source model and invalidates the proxy

        filter only. The heavy side-panel rebuilds (folder tree, submitter

        list, status bar, dashboard) are debounced via

        ``_panels_refresh_timer`` so a flurry of clicks coalesces into one

        rebuild ~120 ms after the last click.



        Fallback (records is None or empty): full reset path used by loaders

        and large bulk operations where per-row notify is no win.

        """

        if records:

            for rec in records:

                self._source_model.notify_record(rec)

            # A status/license/contributor change may make the row pass or

            # fail an active filter — re-evaluate filter without resetting

            # the model.

            self._proxy.invalidateFilter()

            self._panels_refresh_timer.start()

            return

        # Full refresh (initial load, big imports, etc.)

        self._source_model.beginResetModel()

        self._source_model.endResetModel()

        self._refresh_side_panels()



    def _refresh_side_panels(self) -> None:

        """Rebuild folder tree, submitter list, status bar, and dashboard.



        Invoked directly on full refreshes and via ``_panels_refresh_timer``

        after debounced per-row mutations.

        """

        self._rebuild_folder_tree(self._records)

        self._rebuild_submitter_list(self._records)

        self._update_status_bar()

        cur_confirmed = sum(1 for r in self._records if r.status == "confirmed")

        self._session_confirmed = max(0, cur_confirmed - self._load_confirmed)

        self._dashboard.refresh(self._records, self._session_confirmed, None)



    def _quick_confirm_author(self, rec: FileRecord) -> None:

        """Toggle author confirmation for this record."""

        before = _RecordSnapshot.capture(rec)

        if rec.author_confirmed:

            rec.author_confirmed = False

            # Author no longer asserted: an awaiting-license row drops back to

            # gap so save removes it from the ledger (contributor stays as hint).

            if rec.status == "awaiting_license":

                rec.status = "gap"

        else:

            if not rec.contributor:

                rec.contributor = default_contributor(rec)

            if not rec.contributor:

                return

            rec.author_confirmed = True

            # Confirmed author without a license => awaiting_license, which is

            # what save() persists; gap rows would silently be dropped.

            _recompute_status_for_author_license(rec)

        rec.dirty = True

        after = _RecordSnapshot.capture(rec)

        self._undo_stack.push(_RecordEditCommand(

            [rec], [before], [after], self._source_model,

            self._refresh_after_mutation, "Toggle author confirmation"

        ))



    def _quick_assign_license(self, rec: FileRecord, lic: str) -> None:

        """Set license from the clicked button, confirm the record."""

        before = _RecordSnapshot.capture(rec)

        rec.license = lic

        rec.status = "confirmed"

        rec.contributor = contributor_for_license(rec, lic, rec.contributor or default_contributor(rec))

        if rec.contributor:

            rec.author_confirmed = True

        rec.dirty = True

        after = _RecordSnapshot.capture(rec)

        self._undo_stack.push(_RecordEditCommand(

            [rec], [before], [after], self._source_model,

            self._refresh_after_mutation, f"Assign {lic}"

        ))



    def _quick_clear_record(self, rec: FileRecord) -> None:

        """Reset a record back to gap — clears license, contributor, status."""

        before = _RecordSnapshot.capture(rec)

        rec.status = "gap"

        rec.license = ""

        rec.contributor = ""

        rec.author_confirmed = False

        rec.dirty = True

        after = _RecordSnapshot.capture(rec)

        self._undo_stack.push(_RecordEditCommand(

            [rec], [before], [after], self._source_model,

            self._refresh_after_mutation, "Clear record"

        ))



    def _bulk_confirm_selected(self) -> None:

        """Confirm all selected gap/intent rows that have a known author."""

        lic = self._bulk_license_combo.currentData()

        recs = self._selected_records()

        eligible: list[tuple] = []

        for rec in recs:

            if rec.status in ("gap", "intent"):

                author = rec.contributor or rec.unitdef_author or rec.proxy_submitter or rec.git_author

                if author:

                    eligible.append((rec, author))

        if not eligible:

            self._flash_status("No eligible selected records to confirm.")

            return

        befores = [_RecordSnapshot.capture(r) for r, _ in eligible]

        for rec, author in eligible:

            rec.contributor = contributor_for_license(rec, lic, author)

            rec.author_confirmed = True

            rec.license = lic

            rec.status = "confirmed"

            rec.dirty = True

        afters = [_RecordSnapshot.capture(r) for r, _ in eligible]

        recs_list = [r for r, _ in eligible]

        self._undo_stack.push(_RecordEditCommand(

            recs_list, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Bulk confirm selected {len(recs_list)} file(s)"

        ))

        self._flash_status(f"Bulk confirmed {len(recs_list)} selected record(s).")



    def _bulk_author_selected(self) -> None:

        """Confirm author on all selected rows."""

        recs = self._selected_records()

        eligible = [(r, r.contributor or r.unitdef_author or r.proxy_submitter or r.git_author) for r in recs

                    if (r.contributor or r.unitdef_author or r.proxy_submitter or r.git_author)]

        if not eligible:

            self._flash_status("No selected records with a known author.")

            return

        befores = [_RecordSnapshot.capture(r) for r, _ in eligible]

        for rec, author in eligible:

            rec.contributor = author

            rec.author_confirmed = True

            _recompute_status_for_author_license(rec)

            rec.dirty = True

        afters = [_RecordSnapshot.capture(r) for r, _ in eligible]

        self._undo_stack.push(_RecordEditCommand(

            [r for r, _ in eligible], befores, afters, self._source_model,

            self._refresh_after_mutation, f"Bulk author {len(eligible)} file(s)"

        ))

        self._flash_status(f"Set author on {len(eligible)} record(s).")



    def _bulk_license_selected(self, lic: str) -> None:

        """Assign a specific license to all selected rows and confirm them."""

        recs = self._selected_records()

        if not recs:

            self._flash_status("No records selected.")

            return

        befores = [_RecordSnapshot.capture(r) for r in recs]

        for rec in recs:

            rec.license = lic

            rec.status = "confirmed"

            rec.contributor = contributor_for_license(rec, lic, rec.contributor or default_contributor(rec))

            rec.dirty = True

        afters = [_RecordSnapshot.capture(r) for r in recs]

        self._undo_stack.push(_RecordEditCommand(

            recs, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Bulk assign {lic} {len(recs)} file(s)"

        ))

        self._flash_status(f"Assigned {lic} to {len(recs)} record(s).")



    def _open_flag_wizard(self) -> None:

        """Edit -> Flag Wizard: manage custom flag definitions."""

        dlg = FlagWizardDialog(self)

        if dlg.exec() != QDialog.DialogCode.Accepted:

            return

        new_defs = dlg.result_defs()

        if new_defs == list(CUSTOM_FLAG_DEFS):

            return

        set_custom_flag_defs(new_defs)

        self._flag_defs_dirty = True

        self._rebuild_flag_choice_widgets()

        self._source_model.beginResetModel()

        self._source_model.endResetModel()

        self._update_status_bar()

        self._flash_status("Flag definitions updated — save (Ctrl+S) to persist.")

    def _rebuild_flag_choice_widgets(self) -> None:

        """Repopulate flag-dependent widgets after the registry changed."""

        if hasattr(self, "_flag_filter_combo"):

            cur = self._flag_filter_combo.currentData()

            self._flag_filter_combo.blockSignals(True)

            self._flag_filter_combo.clear()

            self._flag_filter_combo.addItem("Any flag", None)

            self._flag_filter_combo.addItem("(none)", "")

            for fv, fl, _fc in FLAG_DEFS:

                if fv == "":

                    continue

                self._flag_filter_combo.addItem(fl, fv)

            idx = self._flag_filter_combo.findData(cur)

            self._flag_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)

            self._flag_filter_combo.blockSignals(False)

        if hasattr(self, "_bulk_flag_combo"):

            cur = self._bulk_flag_combo.currentData()

            self._bulk_flag_combo.blockSignals(True)

            self._bulk_flag_combo.clear()

            for fv, fl, fc in FLAG_DEFS:

                self._bulk_flag_combo.addItem(fl, fv)

                self._bulk_flag_combo.setItemData(self._bulk_flag_combo.count() - 1, QColor(fc),

                                                  Qt.ItemDataRole.ForegroundRole)

            idx = self._bulk_flag_combo.findData(cur)

            self._bulk_flag_combo.setCurrentIndex(idx if idx >= 0 else 0)

            self._bulk_flag_combo.blockSignals(False)

        self._detail_panel.rebuild_flag_choices()

    def _bulk_flag_selected(self) -> None:

        """Bottom-bar Flag button: add the combo's flag to all selected rows."""

        recs = self._selected_records()

        if not recs:

            self._flash_status("No records selected.")

            return

        fv = self._bulk_flag_combo.currentData() or ""

        if not fv:

            self._bulk_clear_flags(recs)

        else:

            self._bulk_toggle_flag(recs, fv, add=True)

    def _bulk_clear_selected(self) -> None:

        """Reset all selected rows back to gap."""

        recs = self._selected_records()

        if not recs:

            self._flash_status("No records selected.")

            return

        befores = [_RecordSnapshot.capture(r) for r in recs]

        for rec in recs:

            rec.status = "gap"

            rec.license = ""

            rec.contributor = ""

            rec.author_confirmed = False

            rec.dirty = True

        afters = [_RecordSnapshot.capture(r) for r in recs]

        self._undo_stack.push(_RecordEditCommand(

            recs, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Bulk clear {len(recs)} file(s)"

        ))

        self._flash_status(f"Cleared {len(recs)} record(s).")



    def _bulk_confirm(self) -> None:

        """Confirm all currently visible gap/intent rows that have a known author."""

        lic = self._bulk_license_combo.currentData()

        eligible: list[tuple] = []

        for row in range(self._proxy.rowCount()):

            src = self._proxy.mapToSource(self._proxy.index(row, 0))

            rec: FileRecord = self._source_model.record_at_source_row(src.row())

            if rec.status in ("gap", "intent"):

                author = rec.contributor or rec.unitdef_author or rec.proxy_submitter or rec.git_author

                if author:

                    eligible.append((rec, author))

        if not eligible:

            self._flash_status("No eligible records to confirm.")

            return

        befores = [_RecordSnapshot.capture(r) for r, _ in eligible]

        for rec, author in eligible:

            rec.contributor = contributor_for_license(rec, lic, author)

            rec.license = lic

            rec.status = "confirmed"

            rec.dirty = True

        afters = [_RecordSnapshot.capture(r) for r, _ in eligible]

        recs = [r for r, _ in eligible]

        self._undo_stack.push(_RecordEditCommand(

            recs, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Bulk confirm {len(recs)} file(s)"

        ))

        self._flash_status(f"Bulk confirmed {len(recs)} record(s).")



    # ------------------------------------------------------------------

    # Keyboard shortcut handlers (Session 2)

    # ------------------------------------------------------------------



    def _key_confirm_author(self) -> None:

        """Key 1 — confirm git_author (or unitdef_author) as contributor for selected rows."""

        recs = self._selected_records()

        eligible = [

            (r, r.contributor or r.unitdef_author or r.proxy_submitter or r.git_author)

            for r in recs

            if (r.contributor or r.unitdef_author or r.proxy_submitter or r.git_author)

        ]

        if not eligible:

            self._flash_status("[1] No selected records with a known author.")

            return

        befores = [_RecordSnapshot.capture(r) for r, _ in eligible]

        for rec, author in eligible:

            rec.contributor = author

            rec.author_confirmed = True

            _recompute_status_for_author_license(rec)

            rec.dirty = True

        afters = [_RecordSnapshot.capture(r) for r, _ in eligible]

        self._undo_stack.push(_RecordEditCommand(

            [r for r, _ in eligible], befores, afters, self._source_model,

            self._refresh_after_mutation, f"Key-confirm author [{len(eligible)}]"

        ))

        self._flash_status(f"[1] Set author on {len(eligible)} record(s).")



    def _key_assign_license(self, lic: str) -> None:

        """Keys 2/3/4 — assign a license to all selected rows and confirm them."""

        recs = self._selected_records()

        if not recs:

            self._flash_status("No rows selected.")

            return

        befores = [_RecordSnapshot.capture(r) for r in recs]

        for rec in recs:

            rec.license = lic

            rec.status = "confirmed"

            rec.contributor = contributor_for_license(rec, lic, rec.contributor or default_contributor(rec))

            if rec.contributor:

                rec.author_confirmed = True

            rec.dirty = True

        afters = [_RecordSnapshot.capture(r) for r in recs]

        self._undo_stack.push(_RecordEditCommand(

            recs, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Key-assign {lic} [{len(recs)}]"

        ))

        self._flash_status(f"Assigned {lic} to {len(recs)} record(s).")



    def _nav_next_gap(self, forward: bool = True) -> None:

        """N / Shift+N — jump to the next or previous gap row in the current view."""

        proxy = self._proxy

        n = proxy.rowCount()

        if n == 0:

            self._flash_status("No rows in current view.")

            return

        sel_rows = self._table.selectionModel().selectedRows()

        current = sel_rows[0].row() if sel_rows else -1



        if forward:

            candidates = list(range(current + 1, n)) + list(range(0, current + 1))

        else:

            candidates = list(range(current - 1, -1, -1)) + list(range(n - 1, current - 1, -1))



        for row in candidates:

            rec = proxy.source_record(proxy.index(row, 0))

            if rec and rec.status == "gap":

                self._table.selectRow(row)

                self._table.scrollTo(proxy.index(row, 0),

                                     QAbstractItemView.ScrollHint.EnsureVisible)

                self._flash_status(f"Gap: {rec.path}")

                return

        self._flash_status("No gap rows found in current view.")




    # ------------------------------------------------------------------

    # Detail panel (Session 4)

    # ------------------------------------------------------------------



    def _toggle_detail_panel(self) -> None:

        """Ctrl+D — show or hide the detail panel."""

        was_visible = self._detail_panel.isVisible()

        self._detail_panel.setVisible(not was_visible)

        if not was_visible:

            # Splitter may have restored a 0-width slot for the detail pane

            # (e.g. it was hidden when state was last saved). Ensure it gets

            # a usable width so it actually appears.

            sizes = self._main_splitter.sizes()

            if len(sizes) >= 3 and sizes[2] < 80:

                want = 280

                # Steal from the table column (index 1) without shrinking it

                # below a reasonable minimum.

                if sizes[1] - want > 400:

                    sizes[1] -= want

                else:

                    want = max(220, sizes[1] - 400)

                    sizes[1] -= want

                sizes[2] = want

                self._main_splitter.setSizes(sizes)

            self._update_detail_panel()

            self._detail_panel.refresh_thumbnail()



    def _update_detail_panel(self) -> None:

        """Refresh detail panel content for current selection."""

        recs = self._selected_records()

        if recs:

            self._detail_panel.show_record(recs[0])

        else:

            self._detail_panel.clear()



    def _filter_by_author(self, author: str) -> None:

        """Set submitter filter to *author* (triggered from detail panel)."""

        for i in range(self._submitter_list.count()):

            item = self._submitter_list.item(i)

            if item.data(Qt.ItemDataRole.UserRole) == author:

                self._submitter_list.clearSelection()

                self._submitter_list.setCurrentItem(item)

                return



    # ------------------------------------------------------------------

    # Flags + comments edit handlers (driven from _DetailPanel signals)

    # ------------------------------------------------------------------



    def _get_local_username(self) -> str:

        """Best-effort local author name for new comments. Falls back to ``anon``."""

        if hasattr(self, "_cached_local_user") and self._cached_local_user:

            return self._cached_local_user

        name = ""

        try:

            r = subprocess.run(

                ["git", "config", "user.name"], capture_output=True, text=True,

                cwd=self._repo_root, timeout=3,

            )

            name = (r.stdout or "").strip()

        except Exception:

            name = ""

        if not name:

            import os as _os

            name = _os.environ.get("USERNAME") or _os.environ.get("USER") or "anon"

        self._cached_local_user = name

        return name



    def _set_record_flag(self, rec, new_flags: list) -> None:

        new_flags = normalize_flags(new_flags)

        if new_flags == (rec.flags or []):

            return

        before = _RecordSnapshot.capture(rec)

        rec.flags = new_flags

        rec.flags_comments_dirty = True

        after = _RecordSnapshot.capture(rec)

        self._undo_stack.push(_RecordEditCommand(

            [rec], [before], [after], self._source_model,

            self._refresh_after_mutation, "Set flag",

        ))

        self._refresh_after_mutation([rec])

        self._detail_panel.refresh_flag_and_comments()



    def _add_record_comment(self, rec, text: str) -> None:

        text = (text or "").strip()

        if not text:

            return

        before = _RecordSnapshot.capture(rec)

        new_comments = list(rec.comments or [])

        new_comments.append({

            "author": self._get_local_username(),

            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),

            "text": text,

        })

        rec.comments = new_comments

        rec.flags_comments_dirty = True

        after = _RecordSnapshot.capture(rec)

        self._undo_stack.push(_RecordEditCommand(

            [rec], [before], [after], self._source_model,

            self._refresh_after_mutation, "Add comment",

        ))

        self._refresh_after_mutation([rec])

        self._detail_panel.refresh_flag_and_comments()



    def _delete_record_comment(self, rec, index: int) -> None:

        comments = list(rec.comments or [])

        if index < 0 or index >= len(comments):

            return

        before = _RecordSnapshot.capture(rec)

        del comments[index]

        rec.comments = comments

        rec.flags_comments_dirty = True

        after = _RecordSnapshot.capture(rec)

        self._undo_stack.push(_RecordEditCommand(

            [rec], [before], [after], self._source_model,

            self._refresh_after_mutation, "Delete comment",

        ))

        self._refresh_after_mutation([rec])

        self._detail_panel.refresh_flag_and_comments()



    def _maybe_auto_promote_awaiting(self, author: str, license_val: str, exclude=None) -> None:

        """When user picks a license for an author, offer to apply it to all

        their other awaiting-license records."""

        if not author or not license_val:

            return

        candidates = [

            r for r in self._records

            if r.status == "awaiting_license"

            and r is not exclude

            and (r.contributor or "").lower() == author.lower()

        ]

        if not candidates:

            return

        reply = QMessageBox.question(

            self, "Apply license to all awaiting files?",

            f"{author} has {len(candidates)} other file(s) marked 'awaiting license'.\n\n"

            f"Apply '{license_val}' to all of them now?",

            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,

            QMessageBox.StandardButton.Yes,

        )

        if reply != QMessageBox.StandardButton.Yes:

            return

        befores = [_RecordSnapshot.capture(r) for r in candidates]

        from .data import apply_license_to_author_awaiting as _apply

        mutated = _apply(self._records, author, license_val)

        # apply_license_to_author_awaiting mutated candidates in-place (incl. exclude? no:

        # status was already 'confirmed', filter skips). Snapshot afters from candidates.

        afters = [_RecordSnapshot.capture(r) for r in candidates]

        self._undo_stack.push(_RecordEditCommand(

            candidates, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Apply license to {len(candidates)}",

        ))

        self._refresh_after_mutation(candidates)



    def _jump_to_dir(self, top_dir: str) -> None:

        """Switch to Dirs tab and select *top_dir* in the folder list (dashboard click)."""

        self._left_tabs.setCurrentIndex(0)

        for i in range(self._folder_list.count()):

            item = self._folder_list.item(i)

            if item.data(Qt.ItemDataRole.UserRole) == top_dir:

                self._folder_list.setCurrentItem(item)

                return

        # Fallback: select "All art files"

        if self._folder_list.count() > 0:

            self._folder_list.setCurrentItem(self._folder_list.item(0))



    def _show_readme(self) -> None:

        """Help > README — open README.md in a scrollable dialog."""

        readme_path = Path(__file__).parent / "README.md"

        try:

            text = readme_path.read_text(encoding="utf-8")

        except Exception as exc:

            QMessageBox.warning(self, "README", f"Could not read README: {exc}")

            return

        dlg = QDialog(self)

        dlg.setWindowTitle("BAR License Auditor — README")

        dlg.resize(760, 620)

        layout = QVBoxLayout(dlg)

        layout.setContentsMargins(20, 16, 20, 12)

        layout.setSpacing(12)

        view = QTextEdit()

        view.setReadOnly(True)

        view.setMarkdown(text)

        view.document().setDocumentMargin(16)

        view.document().setDefaultStyleSheet(

            "h1 { margin-top: 20px; } h2 { margin-top: 16px; } h3 { margin-top: 12px; }"

        )

        _f = view.font()

        _f.setPointSize(11)

        view.setFont(_f)

        layout.addWidget(view)

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)

        btn.rejected.connect(dlg.accept)

        layout.addWidget(btn)

        dlg.exec()



    def _toggle_help_overlay(self) -> None:

        """F1 / ? — toggle the keybinding help overlay."""

        if self._help_overlay.isVisible():

            self._help_overlay.setVisible(False)

        else:

            self._help_overlay.show_overlay()



    # ------------------------------------------------------------------

    # Selection

    # ------------------------------------------------------------------



    def _on_selection_changed(self):

        n = len(self._table.selectionModel().selectedRows())

        self._sel_label.setText(f"{n} selected" if n else "No selection")

        self._edit_btn.setEnabled(n > 0)

        if self._detail_panel.isVisible():

            self._update_detail_panel()



    def _selected_records(self) -> list[FileRecord]:

        rows = [idx.row() for idx in self._table.selectionModel().selectedRows()]

        return self._proxy.source_records_for_proxy_rows(rows)



    def _visible_records(self) -> list[FileRecord]:

        """All records currently passing the active filter (all proxy rows)."""

        return self._proxy.source_records_for_proxy_rows(

            list(range(self._proxy.rowCount()))

        )



    # ------------------------------------------------------------------

    # Session 7: Export / Import CSV

    # ------------------------------------------------------------------



    def _export_csv(self) -> None:

        """File → Export CSV: write all currently-visible rows to a CSV file."""

        recs = self._visible_records()

        if not recs:

            self._flash_status("No rows to export.")

            return

        path, _ = QFileDialog.getSaveFileName(

            self, "Export CSV", "audit_export.csv", "CSV files (*.csv)"

        )

        if not path:

            return

        with open(path, "w", newline="", encoding="utf-8") as f:

            writer = csv.writer(f)

            writer.writerow(["Status", "File", "License", "Contributor",

                             "Submitter", "Reference", "Date", "Last Changed"])

            for r in recs:

                writer.writerow([

                    r.status, r.path, r.license, r.contributor,

                    r.proxy_submitter or r.git_author,

                    r.pr, r.date, r.last_changed,

                ])

        self._flash_status(f"Exported {len(recs)} rows → {Path(path).name}")



    def _import_csv(self) -> None:

        """File → Import CSV: map columns, preview matches, apply as undoable mutation."""

        if not self._records:

            self._flash_status("Load a repo first.")

            return

        path, _ = QFileDialog.getOpenFileName(

            self, "Import CSV", "", "CSV files (*.csv);;All files (*)"

        )

        if not path:

            return

        dlg = _CsvImportDialog(path, self._records, self)

        if dlg.exec() != QDialog.DialogCode.Accepted:

            return

        befores = [_RecordSnapshot.capture(r) for r in self._records]

        count = dlg.apply()

        if count == 0:

            self._flash_status("Import: no matching records updated.")

            return

        afters = [_RecordSnapshot.capture(r) for r in self._records]

        self._undo_stack.push(_RecordEditCommand(

            self._records, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Import CSV ({count} records)",

        ))

        self._flash_status(f"Imported {count} record(s) from {Path(path).name}.")



    # ------------------------------------------------------------------

    # Session 7: Mark all filtered

    # ------------------------------------------------------------------



    def _mark_all_filtered(self) -> None:

        """Toolbar: bulk-edit ALL currently-visible rows."""

        recs = self._visible_records()

        if not recs:

            self._flash_status("No visible rows to edit.")

            return

        reply = QMessageBox.question(

            self, "Mark all filtered",

            f"Open bulk-edit dialog for all {len(recs)} visible file(s)?",

            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,

        )

        if reply != QMessageBox.StandardButton.Yes:

            return

        shared_top_dirs = {r.top_dir for r in recs}

        dir_license_hint = (self._get_dir_license(next(iter(shared_top_dirs)))

                            if len(shared_top_dirs) == 1 else "")

        dlg = BulkEditDialog(recs, self, dir_license_hint=dir_license_hint)

        if dlg.exec() != QDialog.DialogCode.Accepted:

            return

        befores = [_RecordSnapshot.capture(r) for r in recs]

        count = dlg.apply()

        afters = [_RecordSnapshot.capture(r) for r in recs]

        self._undo_stack.push(_RecordEditCommand(

            recs, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Mark all filtered ({count})",

        ))

        self._detail_panel.refresh_flag_and_comments()

        lic = getattr(dlg, "_accepted_license", "")

        if lic:

            for td in shared_top_dirs:

                self._save_dir_license(td, lic)

        self._flash_status(f"Modified {count} of {len(recs)} visible record(s).")



    # ------------------------------------------------------------------

    # Session 7: Copy as TSV

    # ------------------------------------------------------------------



    # Copyable columns: filename, contributor, submitter, PR, date, last changed

    _COPYABLE_COLS: dict[int, str] = {

        COL_FILE: "Filename",

        COL_CONTRIBUTOR: "Contributor",

        COL_SUBMITTER: "Submitter",

        COL_PR: "Reference",

        COL_DATE: "Date",

        COL_LAST_CHANGED: "Last Changed",

        COL_FLAG: "Flags",

    }



    def _copy_current_cell(self) -> None:

        """Ctrl+C on the table: copy the current cell's text if it's a copyable column."""

        idx = self._table.currentIndex()

        if not idx.isValid():

            return

        col = idx.column()

        if col not in self._COPYABLE_COLS:

            return

        text = self._proxy.data(idx)

        if text:

            QApplication.clipboard().setText(str(text))

            self._flash_status(f"Copied: {text}")



    def _copy_tsv(self) -> None:

        """Ctrl+Shift+C: copy selected (or all visible) rows to clipboard as TSV."""

        recs = self._selected_records() or self._visible_records()

        if not recs:

            self._flash_status("Nothing to copy.")

            return

        header = "\t".join(["Status", "File", "License", "Contributor",

                             "Submitter", "Reference", "Date", "Last Changed"])

        lines = [header]

        for r in recs:

            lines.append("\t".join([

                r.status, r.path, r.license, r.contributor,

                r.proxy_submitter or r.git_author,

                r.pr, r.date, r.last_changed,

            ]))

        QApplication.clipboard().setText("\n".join(lines))

        self._flash_status(f"Copied {len(recs)} row(s) to clipboard as TSV.")



    # ------------------------------------------------------------------

    # Session 7: Select all same-extension in directory

    # ------------------------------------------------------------------



    def _select_same_ext(self, ref_rec: FileRecord) -> None:

        """Select all visible rows sharing *ref_rec*'s extension and top_dir."""

        ext = Path(ref_rec.path).suffix.lower()

        top_dir = ref_rec.top_dir

        sel = self._table.selectionModel()

        sel.clearSelection()

        for proxy_row in range(self._proxy.rowCount()):

            src = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))

            r = self._source_model.record_at_source_row(src.row())

            if r.top_dir == top_dir and Path(r.path).suffix.lower() == ext:

                sel.select(

                    self._proxy.index(proxy_row, 0),

                    QItemSelectionModel.SelectionFlag.Select

                    | QItemSelectionModel.SelectionFlag.Rows,

                )

        count = len(self._table.selectionModel().selectedRows())

        self._flash_status(f"Selected {count} *{ext} file(s) in {top_dir}/.")



    def _context_menu(self, pos):

        recs = self._selected_records()

        if not recs:

            return

        menu = QMenu(self)

        # Copy single-cell value for copyable columns

        clicked_idx = self._table.indexAt(pos)

        if clicked_idx.isValid() and clicked_idx.column() in self._COPYABLE_COLS:

            col_name = self._COPYABLE_COLS[clicked_idx.column()]

            cell_text = self._proxy.data(clicked_idx) or ""

            act_copy = menu.addAction(f"Copy {col_name}: {cell_text!r}")

            act_copy.triggered.connect(lambda checked, t=cell_text: QApplication.clipboard().setText(t))

            menu.addSeparator()

        menu.addAction(f"Edit {len(recs)} file(s)…", self._edit_selected)
        menu.addAction(f"Mark {len(recs)} file(s) as purchased…", self._mark_purchased)
        menu.addAction(f"Mark {len(recs)} file(s) as owned work…", self._mark_owned)

        # Open PR in browser (single-row only)

        if len(recs) == 1:

            rec = recs[0]

            pr_url = rec.pr_url or (

                f"https://github.com/beyond-all-reason/Beyond-All-Reason/pull/{rec.pr}"

                if rec.pr and rec.pr.isdigit() else ""

            )

            if pr_url:

                act_pr = menu.addAction(f"Open PR #{rec.pr} in browser")

                act_pr.triggered.connect(lambda checked, u=pr_url: QDesktopServices.openUrl(QUrl(u)))

            elif rec.commit_url:

                short = rec.commit_hash[:7] if rec.commit_hash else ""

                act_pr = menu.addAction(f"Open commit {short} in browser")

                act_pr.triggered.connect(lambda checked, u=rec.commit_url: QDesktopServices.openUrl(QUrl(u)))

            # Select all same-extension in this directory

            ext = Path(rec.path).suffix.lower()

            if ext:

                act_ext = menu.addAction(

                    f"Select all *{ext} in {rec.top_dir}/"

                )

                act_ext.triggered.connect(lambda checked, r=rec: self._select_same_ext(r))

        menu.addSeparator()



        # Quick-assign submenu for common licenses

        from .data import KNOWN_LICENSES

        quick = menu.addMenu("Quick assign license")

        for lic in KNOWN_LICENSES:

            act = quick.addAction(lic)

            act.triggered.connect(lambda checked, l=lic: self._quick_license(l))



        # Flags submenu - checkable: checked when every selected row has the

        # flag; toggling adds it to / removes it from all selected rows.

        flag_menu = menu.addMenu("Flags")

        for fv, fl, _fc in FLAG_DEFS:

            if fv == "":

                continue

            act_f = flag_menu.addAction(fl)

            act_f.setCheckable(True)

            act_f.setChecked(all(fv in (r.flags or []) for r in recs))

            act_f.triggered.connect(

                lambda checked, f=fv, rs=recs: self._bulk_toggle_flag(rs, f, add=checked))

        flag_menu.addSeparator()

        act_clear_flags = flag_menu.addAction("Clear all flags")

        act_clear_flags.triggered.connect(lambda checked, rs=recs: self._bulk_clear_flags(rs))



        # Fill / confirm contributor from unitdef model author (objects3d, unitpics,

        # unittextures incl. decals); tentative (unconfirmed) authors qualify too.

        obj3d_recs = [r for r in recs if r.unitdef_author and (not r.contributor or not r.author_confirmed)]

        if obj3d_recs:

            menu.addSeparator()

            label = (f"Fill contributor from unitdef ({len(obj3d_recs)} file(s))")

            act_ud = menu.addAction(label)

            act_ud.triggered.connect(lambda checked, rs=obj3d_recs: self._fill_from_unitdef(rs))



        menu.exec(self._table.viewport().mapToGlobal(pos))



    def _quick_license(self, license: str):

        recs = self._selected_records()

        if not recs:

            return

        befores = [_RecordSnapshot.capture(r) for r in recs]

        for rec in recs:

            if rec.license != license:

                rec.license = license

                rec.dirty = True

                _recompute_status_for_author_license(rec)

        afters = [_RecordSnapshot.capture(r) for r in recs]

        self._undo_stack.push(_RecordEditCommand(

            recs, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Assign {license}"

        ))

        self._refresh_after_mutation(recs)



    def _bulk_toggle_flag(self, recs: list, flag: str, add: bool) -> None:

        """Add *flag* to (or remove it from) every record in *recs*."""

        if not recs or not flag:

            return

        befores = [_RecordSnapshot.capture(r) for r in recs]

        for rec in recs:

            has = flag in (rec.flags or [])

            if add and not has:

                rec.flags = normalize_flags((rec.flags or []) + [flag])

                rec.flags_comments_dirty = True

            elif not add and has:

                rec.flags = [f for f in rec.flags if f != flag]

                rec.flags_comments_dirty = True

        afters = [_RecordSnapshot.capture(r) for r in recs]

        label = FLAG_LABEL.get(flag, flag)

        verb = "Add" if add else "Remove"

        self._undo_stack.push(_RecordEditCommand(

            recs, befores, afters, self._source_model,

            self._refresh_after_mutation, f"{verb} flag: {label}"

        ))

        self._refresh_after_mutation(recs)

        self._detail_panel.refresh_flag_and_comments()

    def _bulk_clear_flags(self, recs: list) -> None:

        """Remove every flag from the given records."""

        recs = [r for r in recs if r.flags]

        if not recs:

            return

        befores = [_RecordSnapshot.capture(r) for r in recs]

        for rec in recs:

            rec.flags = []

            rec.flags_comments_dirty = True

        afters = [_RecordSnapshot.capture(r) for r in recs]

        self._undo_stack.push(_RecordEditCommand(

            recs, befores, afters, self._source_model,

            self._refresh_after_mutation, "Clear flags"

        ))

        self._refresh_after_mutation(recs)

        self._detail_panel.refresh_flag_and_comments()



    def _fill_from_unitdef(self, recs: list) -> None:

        """Confirm contributor = unitdef model author on records that have one.



        Also replaces a tentative (unconfirmed) contributor, e.g. a git author

        picked up before the unitdef correlation covered this file type.

        """

        eligible = [

            r for r in recs

            if r.unitdef_author and (not r.contributor or not r.author_confirmed)

        ]

        if not eligible:

            return

        befores = [_RecordSnapshot.capture(r) for r in eligible]

        for rec in eligible:

            rec.contributor = rec.unitdef_author

            rec.author_confirmed = True

            _recompute_status_for_author_license(rec)

            rec.dirty = True

        afters = [_RecordSnapshot.capture(r) for r in eligible]

        self._undo_stack.push(_RecordEditCommand(

            eligible, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Fill unitdef author [{len(eligible)}]"

        ))

        self._flash_status(f"Filled contributor from unitdef on {len(eligible)} file(s).")



    # ------------------------------------------------------------------

    # Edit

    # ------------------------------------------------------------------



    def _related_unit_records(self, recs: list[FileRecord]) -> tuple[list[FileRecord], list[str]]:
        """Unlicensed files of the same units as the selection: wrecks, unitpics, decals."""
        import os
        chosen = {id(r) for r in recs}
        units: set[str] = set()
        for r in recs:
            if r.unitdef_author:
                units.add(unit_key_candidates(os.path.splitext(os.path.basename(r.path))[0])[-1])
        if not units:
            return [], []
        related = []
        for r in self._records:
            if id(r) in chosen or r.license:
                continue
            if unit_key_candidates(os.path.splitext(os.path.basename(r.path))[0])[-1] in units:
                related.append(r)
        return related, sorted(units)

    def _with_related(self, recs: list[FileRecord], what: str) -> list[FileRecord]:
        related, units = self._related_unit_records(recs)
        if not related:
            return recs
        names = ", ".join(units[:6]) + (" \u2026" if len(units) > 6 else "")
        ans = QMessageBox.question(
            self, f"Mark as {what}",
            f"{len(related)} unlicensed file(s) belong to the same unit(s) ({names}): wrecks, unitpics, "
            f"decals or textures named after them.\n\nInclude them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if ans == QMessageBox.StandardButton.Cancel:
            return []
        return recs + related if ans == QMessageBox.StandardButton.Yes else recs

    def _mark_owned(self):
        recs = self._with_related(self._selected_records(), "owned work")
        if not recs:
            return
        dlg = OwnedDialog(recs, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            befores = [_RecordSnapshot.capture(r) for r in recs]
            count = dlg.apply()
            afters = [_RecordSnapshot.capture(r) for r in recs]
            self._undo_stack.push(_RecordEditCommand(
                recs, befores, afters, self._source_model,
                self._refresh_after_mutation, f"Mark {count} file(s) owned"
            ))

    def _mark_purchased(self):
        recs = self._with_related(self._selected_records(), "purchased")
        if not recs:
            return
        dlg = PurchasedDialog(recs, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            befores = [_RecordSnapshot.capture(r) for r in recs]
            count = dlg.apply()
            afters = [_RecordSnapshot.capture(r) for r in recs]
            self._undo_stack.push(_RecordEditCommand(
                recs, befores, afters, self._source_model,
                self._refresh_after_mutation, f"Mark {count} file(s) purchased"
            ))
            self._detail_panel.refresh_flag_and_comments()

    def _edit_selected(self):

        recs = self._selected_records()

        if not recs:

            return

        # Auto-suggest git_author as contributor hint (single shared git author, no existing contributor)

        shared_git = {(r.unitdef_author or r.proxy_submitter or r.git_author) for r in recs

                      if (r.unitdef_author or r.proxy_submitter or r.git_author)}

        all_no_contributor = all(not r.contributor for r in recs)

        git_author_hint = next(iter(shared_git)) if (len(shared_git) == 1 and all_no_contributor) else ""

        # Per-directory license memory hint

        shared_top_dirs = {r.top_dir for r in recs}

        dir_license_hint = (self._get_dir_license(next(iter(shared_top_dirs)))

                            if len(shared_top_dirs) == 1 else "")

        dlg = BulkEditDialog(recs, self,

                             git_author_hint=git_author_hint,

                             dir_license_hint=dir_license_hint)

        if dlg.exec() == QDialog.DialogCode.Accepted:

            befores = [_RecordSnapshot.capture(r) for r in recs]

            count = dlg.apply()

            afters = [_RecordSnapshot.capture(r) for r in recs]

            self._undo_stack.push(_RecordEditCommand(

                recs, befores, afters, self._source_model,

                self._refresh_after_mutation, f"Edit {count} file(s)"

            ))

            self._detail_panel.refresh_flag_and_comments()

            # Save per-directory license memory

            lic = getattr(dlg, "_accepted_license", "")

            if lic:

                for td in shared_top_dirs:

                    self._save_dir_license(td, lic)

            # Cache for Ctrl+L "apply last edit"

            self._last_edit = {

                "license":     getattr(dlg, "_accepted_license", ""),

                "contributor": getattr(dlg, "_accepted_contributor", ""),

                "status":      getattr(dlg, "_accepted_status", ""),

            }

            self._flash_status(f"Modified {count} record(s).")



    # ------------------------------------------------------------------

    # Save / Commit

    # ------------------------------------------------------------------



    def _save_changes(self):

        if not self._repo_root:

            return

        dirty = [r for r in self._records if r.dirty]

        fc_dirty_only = [r for r in self._records if r.flags_comments_dirty and not r.dirty]

        incomplete = [r for r in dirty if r.status == "confirmed" and (not r.license or not r.contributor)]

        if incomplete:

            names = "\n".join(r.path for r in incomplete[:5])

            if len(incomplete) > 5:

                names += f"\n… and {len(incomplete) - 5} more"

            QMessageBox.warning(

                self, "Incomplete records",

                f"The following confirmed records are missing license or contributor:\n{names}\n\n"

                "Fill them in before saving."

            )

            return



        try:

            # Capture before-state while raw_assigns still reflects the pre-save on-disk state

            _before_by_path: dict[str, dict] = {a["path"]: a for a in self._raw_assigns if "path" in a}

            self._pending_commit_records = dirty[:]

            self._pending_commit_before = [_before_by_path.get(r.path, {}) for r in dirty]

            # Capture flag/comment changes (snapshot of disk state BEFORE the write).

            fc_dirty_recs = [r for r in self._records if r.flags_comments_dirty]

            self._pending_commit_fc_records = fc_dirty_recs[:]

            self._pending_commit_fc_before = {

                r.path: dict(self._raw_flags_comments.get(r.path, {})) for r in fc_dirty_recs

            }

            written = save_assignments(self._repo_root, dirty, self._raw_assigns)

            # Refresh in-memory assigns from disk

            assigns_path = self._repo_root / ".github" / "file-license-assignments.json"

            try:

                self._raw_assigns = json.loads(assigns_path.read_text(encoding="utf-8"))

            except Exception:

                pass

            # Always persist flags/comments — these are not gated by "dirty" alone

            # so a user can save commenting state without touching licenses.

            fc_written = save_comments_flags(

                self._repo_root, self._records, self._raw_flags_comments

            )

            # Refresh the in-memory flags_comments snapshot from what we just wrote.

            from .data import load_comments_flags as _load_fc

            _defs_reload, self._raw_flags_comments = _load_fc(self._repo_root)

            set_custom_flag_defs(_defs_reload)

            for r in dirty:

                r.dirty = False

            for r in self._records:

                r.flags_comments_dirty = False

            self._flag_defs_dirty = False

            self._source_model.beginResetModel()

            self._source_model.endResetModel()

            self._update_status_bar()

            self._flash_status(

                f"Saved {written} ledger record(s) + {fc_written} flag/comment entries."

            )

        except Exception as exc:

            QMessageBox.critical(self, "Save error", str(exc))



    def _commit_push(self):

        if not self._repo_root:

            return

        dirty = [r for r in self._records if r.dirty]

        has_uncommitted = self._has_git_uncommitted()

        fc_dirty = any(r.flags_comments_dirty for r in self._records) or getattr(self, "_flag_defs_dirty", False)

        if not dirty and not fc_dirty and not has_uncommitted:

            QMessageBox.information(self, "Nothing to commit", "No unsaved changes.")

            return

        if dirty or fc_dirty:

            # _save_changes captures _pending_commit_records/before before writing

            self._save_changes()

            if any(r.dirty for r in self._records):

                # save_changes aborted (validation failure)

                return

        # Use records from last save for the commit dialog preview

        commit_records = self._pending_commit_records

        commit_before = self._pending_commit_before

        dlg = CommitDialog(

            self._repo_root, commit_records, self,

            before_state=commit_before,

            fc_records=self._pending_commit_fc_records,

            fc_before=self._pending_commit_fc_before,

        )

        dlg.exec()

        if dlg.success:

            # Commit succeeded; clear dirty flags and pending state

            for r in self._records:

                r.dirty = False

            self._pending_commit_records = []

            self._pending_commit_before = None

            self._pending_commit_fc_records = []

            self._pending_commit_fc_before = {}

            self._update_status_bar()

        else:

            # Commit cancelled or failed; restore dirty flags so user can try again

            for r in self._pending_commit_records:

                r.dirty = True

            self._update_status_bar()



    # ------------------------------------------------------------------

    # Status bar helpers

    # ------------------------------------------------------------------



    def _update_status_bar(self):

        visible = self._proxy.rowCount()

        total = len(self._records)

        gaps = sum(1 for r in self._records if r.status == "gap")

        intent = sum(1 for r in self._records if r.status == "intent")

        confirmed = sum(1 for r in self._records if r.status == "confirmed")

        dirty = sum(1 for r in self._records if r.dirty)



        merged = sum(1 for r in self._records if r.status == "merged")

        text = (

            f"Showing {visible} of {total} files  |  "

            f"Gap: {gaps}  Intent: {intent}  Confirmed: {confirmed}  Merged: {merged}"

        )

        if dirty:

            text += f"  |  ⚠ {dirty} unsaved change(s)"



        self._sb_label.setText(text)

        has_dirty = dirty > 0

        has_fc_dirty = any(r.flags_comments_dirty for r in self._records) or getattr(self, "_flag_defs_dirty", False)

        has_uncommitted = self._has_git_uncommitted()

        self._save_btn.setEnabled(has_dirty or has_fc_dirty)

        self._commit_btn.setEnabled(has_dirty or has_fc_dirty or has_uncommitted)

        self._rebuild_chips()



    def _has_git_uncommitted(self) -> bool:
        """Return True if the tracked JSON files have uncommitted git changes."""
        if not self._repo_root:
            return False
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain",
                 ".github/cla-signatures.json",
                 ".github/file-license-assignments.json"],
                cwd=str(self._repo_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            return bool(r.stdout.strip())
        except Exception:
            return False

    def _flash_status(self, msg: str, ms: int = 3000):

        self._sb_label.setText(msg)

        QTimer.singleShot(ms, self._update_status_bar)



    # ------------------------------------------------------------------

    # Per-directory license memory

    # ------------------------------------------------------------------



    def _get_dir_license(self, top_dir: str) -> str:

        """Retrieve last-used license for *top_dir* from QSettings."""

        return self._settings.value(f"dirLicense/{top_dir}", "")



    def _save_dir_license(self, top_dir: str, license: str) -> None:

        """Persist last-used license for *top_dir* to QSettings."""

        if top_dir and license:

            self._settings.setValue(f"dirLicense/{top_dir}", license)



    # ------------------------------------------------------------------

    # Apply last bulk edit (Ctrl+L)

    # ------------------------------------------------------------------



    def _apply_last_edit(self) -> None:

        """Ctrl+L — re-apply the most recent BulkEditDialog values to selection."""

        if not self._last_edit:

            self._flash_status("No previous edit to repeat (Ctrl+L).")

            return

        recs = self._selected_records()

        if not recs:

            self._flash_status("No rows selected.")

            return

        le = self._last_edit

        befores = [_RecordSnapshot.capture(r) for r in recs]

        count = 0

        for rec in recs:

            changed = False

            if le.get("license") and rec.license != le["license"]:

                rec.license = le["license"]

                changed = True

            if le.get("contributor") and rec.contributor != le["contributor"]:

                rec.contributor = le["contributor"]

                changed = True

            if le.get("status"):

                if rec.status != le["status"]:

                    rec.status = le["status"]

                    if le["status"] == "gap":

                        rec.license = ""

                        rec.contributor = ""

                    changed = True

            if changed:

                rec.dirty = True

                count += 1

        afters = [_RecordSnapshot.capture(r) for r in recs]

        self._undo_stack.push(_RecordEditCommand(

            recs, befores, afters, self._source_model,

            self._refresh_after_mutation, f"Apply last [{count}]",

        ))

        self._flash_status(f"Applied last edit to {count} record(s).")



    # ------------------------------------------------------------------

    # Session 8: Column visibility/order dialog

    # ------------------------------------------------------------------



    def _open_column_dialog(self) -> None:

        """View → Columns… — show _ColumnDialog to toggle visibility and reorder."""

        from .model import COLUMNS

        dlg = _ColumnDialog(self._table.horizontalHeader(), list(COLUMNS), self)

        dlg.exec()

        # Save new header state immediately

        self._settings.setValue("headerState", self._table.horizontalHeader().saveState())



    # ------------------------------------------------------------------

    # Session 8: Theme (dark / light)

    # ------------------------------------------------------------------



    @staticmethod

    def _make_dark_palette() -> QPalette:

        p = QPalette()

        win      = QColor(30,  34, 45)

        base     = QColor(20,  23, 32)

        alt_base = QColor(26,  30, 42)

        text     = QColor(210, 218, 235)

        disabled = QColor(110, 120, 140)

        highlight     = QColor(42, 90, 170)

        highlight_txt = QColor(255, 255, 255)

        button   = QColor(38, 44, 58)

        border   = QColor(60, 70, 90)

        for role, color in [

            (QPalette.ColorRole.Window,          win),

            (QPalette.ColorRole.WindowText,      text),

            (QPalette.ColorRole.Base,            base),

            (QPalette.ColorRole.AlternateBase,   alt_base),

            (QPalette.ColorRole.Text,            text),

            (QPalette.ColorRole.BrightText,      QColor(255, 255, 255)),

            (QPalette.ColorRole.Button,          button),

            (QPalette.ColorRole.ButtonText,      text),

            (QPalette.ColorRole.Highlight,       highlight),

            (QPalette.ColorRole.HighlightedText, highlight_txt),

            (QPalette.ColorRole.Mid,             border),

            (QPalette.ColorRole.Dark,            QColor(15, 17, 24)),

            (QPalette.ColorRole.Shadow,          QColor(10, 10, 15)),

            (QPalette.ColorRole.Link,            QColor(80, 140, 220)),

            (QPalette.ColorRole.LinkVisited,     QColor(130, 80, 200)),

        ]:

            p.setColor(QPalette.ColorGroup.All, role, color)

        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):

            p.setColor(QPalette.ColorGroup.Disabled, role, disabled)

        return p



    def _apply_theme(self, *, dark: bool, _save: bool = True) -> None:

        """Apply dark or light palette to the application; update checked state of menu items."""

        app = QApplication.instance()

        if dark:

            app.setPalette(self._make_dark_palette())

        else:

            app.setPalette(self._light_palette)

        self._dark_mode = dark

        # Sync checkmarks without triggering triggered signal again

        self._act_dark.blockSignals(True)

        self._act_light.blockSignals(True)

        self._act_dark.setChecked(dark)

        self._act_light.setChecked(not dark)

        self._act_dark.blockSignals(False)

        self._act_light.blockSignals(False)

        if _save:

            self._settings.setValue("darkMode", dark)



    # ------------------------------------------------------------------

    # Session 8: Compact mode

    # ------------------------------------------------------------------



    def _set_compact_mode(self, compact: bool) -> None:

        """View → Compact Mode — reduces row height for dense monitoring."""

        self._compact_mode = compact

        row_h = 18 if compact else 28

        self._table.verticalHeader().setDefaultSectionSize(row_h)

        self._settings.setValue("compactMode", compact)





