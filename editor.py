"""Auto Editor: AI-assisted editor for The Finals recordings."""
import copy
import json
import os
import subprocess
import sys
import threading
import time
import uuid

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDockWidget, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
                               QKeySequenceEdit, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget,
                               QTableWidget, QVBoxLayout, QWidget)

import auto_editor as ae
from timeline import RecordingLane, SequenceView

VIDEO_EXT = (".mkv", ".mp4", ".mov", ".flv", ".avi", ".webm")
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")
SHORTCUTS_FILE = os.path.join(ae.APP_DIR, "shortcuts.json")
MOMENTS_FILE = os.path.join(ae.APP_DIR, "moments.json")
FRAME = 1 / 60

QSS = """
* { color: #e6e6e6; font-family: "Segoe UI"; font-size: 10pt; }
QMainWindow, #central, QDialog { background: #0e0f11; }
QMenuBar { background: #0e0f11; padding: 2px; }
QMenuBar::item { padding: 4px 10px; border-radius: 4px; }
QMenuBar::item:selected, QMenu::item:selected { background: #8b5cf6; color: white; }
QMenu { background: #16181b; border: 1px solid #2a2e34; padding: 4px; }
QMenu::item { padding: 5px 24px 5px 12px; border-radius: 4px; }
QMenu::separator { height: 1px; background: #2a2e34; margin: 4px 8px; }
#toolbar, #transport { background: #121417; border-bottom: 1px solid #1f2329; }
#transport { border-top: 1px solid #1f2329; }
QPushButton { background: #1b1e23; border: 1px solid #2a2e34; border-radius: 6px; padding: 5px 12px; }
QPushButton:hover { border-color: #8b5cf6; }
QPushButton:checked { background: #8b5cf6; border-color: #8b5cf6; color: white; }
QPushButton#primary { background: #8b5cf6; border-color: #8b5cf6; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #7c4dee; }
QPushButton#icon { padding: 5px 9px; min-width: 18px; font-size: 11pt; }
QSlider::groove:horizontal { height: 4px; background: #2a2e34; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #8b5cf6; border-radius: 2px; }
QSlider::handle:horizontal { background: #f2f2f2; width: 14px; margin: -5px 0; border-radius: 7px; }
QStatusBar { background: #121417; color: #8b919b; border-top: 1px solid #1f2329; }
QProgressBar { background: #1b1e23; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #8b5cf6; border-radius: 3px; }
QLabel#lane { color: #6b7280; font-size: 8pt; font-weight: 700; letter-spacing: 1px; padding: 6px 4px 2px 4px; }
QLabel#time { font-family: Consolas; font-size: 12pt; color: #f2f2f2; }
QLabel#hint { color: #6b7280; font-size: 13pt; }
QLineEdit, QComboBox, QSpinBox, QKeySequenceEdit, QTableWidget {
    background: #16181b; border: 1px solid #2a2e34; border-radius: 4px; padding: 3px; }
QTableWidget { gridline-color: #1f2329; }
QHeaderView::section { background: #1b1e23; border: none; padding: 5px; color: #8b919b; }
QComboBox QAbstractItemView { background: #16181b; selection-background-color: #8b5cf6; }
QPlainTextEdit { background: #16181b; border: 1px solid #2a2e34; border-radius: 4px; }
QDockWidget { color: #8b919b; }
QDockWidget::title { background: #121417; padding: 6px 10px; border-bottom: 1px solid #1f2329; }
#momentsBody, QScrollArea { background: #0e0f11; border: none; }
#momentRow { background: #16181b; border: 1px solid #23272d; border-radius: 8px; }
#momentRow:hover { border-color: #3a3f46; }
QLabel#keep { color: #22c55e; font-size: 8pt; font-weight: 700; }
QLabel#cut { color: #ef4444; font-size: 8pt; font-weight: 700; }
QLabel#muted { color: #8b919b; font-size: 9pt; }
QPushButton#small { padding: 2px 8px; font-size: 9pt; }
QLabel#momentName { font-weight: 600; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a3f46; background: #1b1e23; }
QCheckBox::indicator:checked { background: #8b5cf6; border-color: #8b5cf6; }
"""


class MomentsPanel(QScrollArea):
    """Side panel: which kinds of moments the AI should also keep (or cut), with live counts."""

    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="momentsBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        hint = QLabel("Tick what the AI should also keep (or cut). The number is how often it spotted that "
                      "moment in this recording. Changes only touch AI clips you haven't edited or accepted.",
                      objectName="muted", wordWrap=True)
        lay.addWidget(hint)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        lay.addLayout(self.rows)
        add = QPushButton("+  Add your own moment")
        add.setFocusPolicy(Qt.NoFocus)
        add.clicked.connect(lambda: self.ed.edit_moment())
        lay.addWidget(add)
        lay.addStretch()
        self.setWidget(body)
        self.refresh()

    def refresh(self):
        while self.rows.count():
            self.rows.takeAt(0).widget().deleteLater()
        self.rows.addWidget(self.row("Fights", "Set how much with the AI keep slider", self.ed.fights_on,
                                     self.ed.set_fights, "include"))
        for i, m in enumerate(self.ed.moments):
            n = self.ed.found[i] if i < len(self.ed.found) else None
            found = "…" if n is None and self.ed.project else f"{n} found" if n is not None else "–"
            if m["mode"] == "exclude":
                detail = f"{found}  ·  cut whenever it appears"
            else:
                during = "all of it" if m["during"] < 0 else f"{m['during']}s of it"
                detail = f"{found}  ·  {m['before']}s before · {during} · {m['after']}s after"
            self.rows.addWidget(self.row(m["name"], detail, m["enabled"], lambda on, i=i: self.ed.toggle_moment(i, on),
                                         m["mode"], lambda i=i: self.ed.edit_moment(i), lambda i=i: self.ed.delete_moment(i)))

    def row(self, name, detail, checked, on_toggle, mode, on_edit=None, on_delete=None):
        w = QWidget(objectName="momentRow")
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 8, 8, 8)
        v.setSpacing(2)
        top = QHBoxLayout()
        cb = QCheckBox(checked=checked)  # name goes in a wrapping label; checkbox text can't wrap and eats '&'
        cb.setFocusPolicy(Qt.NoFocus)
        cb.toggled.connect(on_toggle)
        label = QLabel(name, wordWrap=True, objectName="momentName")
        label.mousePressEvent = lambda _: cb.toggle()
        top.addWidget(cb)
        top.addWidget(label, 1)
        top.addWidget(QLabel("KEEP" if mode == "include" else "CUT", objectName="keep" if mode == "include" else "cut"))
        for text, slot in (("Edit", on_edit), ("✕", on_delete)):
            if slot:
                b = QPushButton(text, objectName="small")
                b.setFocusPolicy(Qt.NoFocus)
                b.clicked.connect(slot)
                top.addWidget(b)
        v.addLayout(top)
        v.addWidget(QLabel(detail, objectName="muted", wordWrap=True))
        return w


class MomentDialog(QDialog):
    """Add or edit a moment: what it looks like on screen, and how much of it to keep."""

    @classmethod
    def ask(cls, parent, moment=None):
        dlg = cls(parent, moment or {"name": "", "mode": "include", "enabled": True, "sensitivity": 50,
                                     "before": 2, "during": 5, "after": 2, "prompts": []})
        return dlg.result_moment() if dlg.exec() == QDialog.Accepted else None

    def __init__(self, parent, m):
        super().__init__(parent)
        self.m = m
        self.setWindowTitle("Edit moment" if m["name"] else "Add moment")
        self.resize(520, 0)
        form = QFormLayout(self)
        self.name = QLineEdit(m["name"], placeholderText="e.g. Revives")
        self.mode = QComboBox()
        self.mode.addItem("Keep it (add to the edit)", "include")
        self.mode.addItem("Cut it (remove from the edit)", "exclude")
        self.mode.setCurrentIndex(0 if m["mode"] == "include" else 1)
        self.prompts = QPlainTextEdit("\n".join(m["prompts"]))
        self.prompts.setPlaceholderText("Describe what's on screen, one description per line, e.g.\n"
                                        "large TEAM WIPED text on screen\n"
                                        "a player reviving a teammate's statue")
        self.prompts.setFixedHeight(96)
        self.sens = QSlider(Qt.Horizontal, minimum=5, maximum=95, value=m["sensitivity"])
        sens_label = QLabel()
        self.sens.valueChanged.connect(lambda v: sens_label.setText(f"{v}%"))
        self.sens.valueChanged.emit(self.sens.value())
        sens_row = QHBoxLayout()
        sens_row.addWidget(self.sens, 1)
        sens_row.addWidget(sens_label)
        self.before = QSpinBox(minimum=0, maximum=60, value=m["before"], suffix=" s")
        self.during = QSpinBox(minimum=-1, maximum=120, value=m["during"], suffix=" s", specialValueText="all of it")
        self.after = QSpinBox(minimum=0, maximum=60, value=m["after"], suffix=" s")
        form.addRow("Name", self.name)
        form.addRow("When the AI sees it", self.mode)
        form.addRow("What it looks like", self.prompts)
        form.addRow("Sensitivity", sens_row)
        form.addRow("", QLabel("Higher finds more, but also more false matches.", objectName="muted"))
        form.addRow("Keep before it", self.before)
        self.during_label = QLabel("Keep of the moment itself")
        form.addRow(self.during_label, self.during)
        form.addRow("Keep after it", self.after)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.update_mode()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.check)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def update_mode(self):
        cut = self.mode.currentData() == "exclude"
        self.during.setVisible(not cut)
        self.during_label.setVisible(not cut)

    def check(self):
        if not self.name.text().strip() or not self.prompts.toPlainText().strip():
            QMessageBox.warning(self, "Missing info", "Give the moment a name and at least one description.")
            return
        self.accept()

    def result_moment(self):
        return {**self.m, "name": self.name.text().strip(), "mode": self.mode.currentData(),
                "sensitivity": self.sens.value(), "before": self.before.value(),
                "during": -1 if self.mode.currentData() == "exclude" else self.during.value(),
                "after": self.after.value(),
                "prompts": [p.strip() for p in self.prompts.toPlainText().splitlines() if p.strip()]}


class Bridge(QObject):
    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str)


def in_thread(fn, on_progress, on_done, on_fail):
    """Run fn(progress) on a worker thread; callbacks arrive on the UI thread."""
    b = Bridge()
    b.progress.connect(on_progress)
    b.done.connect(on_done)
    b.failed.connect(on_fail)

    def work():
        try:
            b.done.emit(fn(b.progress.emit))
        except Exception as e:
            b.failed.emit(str(e))
    threading.Thread(target=work, daemon=True).start()
    return b  # caller keeps a reference so the signals stay alive


class Editor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project, self.path, self.dirty = None, None, False
        self.duration, self.score, self.analyzing = 0.0, None, None
        self.src_t = self.seq_t = 0.0
        self.mode, self.cur, self.selected, self.snapping = "timeline", 0, None, True
        self.marks = [None, None]
        self.undo_stack, self.redo_stack, self._edit_snap = [], [], None
        self.seek_guard, self.music_cur, self.lengths, self.jobs = 0.0, None, {}, []
        self.exporting = None
        try:
            with open(MOMENTS_FILE) as f:
                saved = json.load(f)
            self.fights_on, self.moments = saved["fights"], saved["moments"]
        except (OSError, ValueError, KeyError):
            self.fights_on, self.moments = True, copy.deepcopy(ae.DEFAULT_MOMENTS)
        self.found, self.ai_gen = [None] * len(self.moments), 0

        self.player, self.audio = QMediaPlayer(), QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.music_player, self.music_audio = QMediaPlayer(), QAudioOutput()
        self.music_player.setAudioOutput(self.music_audio)
        self.video = QVideoWidget()
        self.player.setVideoOutput(self.video)

        self.build_ui()
        self.build_actions()
        self.setAcceptDrops(True)
        self.resize(1400, 900)
        self.refresh()
        self.timer = QTimer(self, interval=15, timeout=self.tick)
        self.timer.start()
        QTimer(self, interval=60_000, timeout=self.autosave).start()

    # ---------- UI ----------
    def build_ui(self):
        central = QWidget(objectName="central")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QWidget(objectName="toolbar")
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(10, 6, 10, 6)
        for text, slot in (("Open", self.open_dialog), ("Save", self.save), ("Add music", self.add_music_dialog)):
            tb.addWidget(self.button(text, slot))
        tb.addSpacing(16)
        tb.addWidget(QLabel("AI keep"))
        self.keep = QSlider(Qt.Horizontal, minimum=10, maximum=80, value=30, fixedWidth=160)
        self.keep_label = QLabel("30%", fixedWidth=36)
        self.keep.valueChanged.connect(lambda v: self.keep_label.setText(f"{v}%"))
        self.keep.sliderReleased.connect(self.resuggest_ai)
        self.keep.setEnabled(self.fights_on)
        tb.addWidget(self.keep)
        tb.addWidget(self.keep_label)
        tb.addWidget(self.button("Accept all", self.accept_all))
        self.moments_btn = self.button("Moments", lambda on: self.dock.setVisible(on), checkable=True)
        self.moments_btn.setChecked(True)
        tb.addWidget(self.moments_btn)
        tb.addStretch()
        self.snap_btn = self.button("Snapping", self.toggle_snap, checkable=True)
        self.snap_btn.setChecked(True)
        tb.addWidget(self.snap_btn)
        tb.addWidget(self.button("Fit", self.fit))
        exp = self.button("Export", self.export_dialog)
        exp.setObjectName("primary")
        tb.addWidget(exp)
        root.addWidget(bar)

        self.viewer = QStackedWidget()
        hint = QLabel("Drop an OBS recording here, or press Ctrl+O", objectName="hint", alignment=Qt.AlignCenter)
        self.viewer.addWidget(hint)
        self.viewer.addWidget(self.video)
        root.addWidget(self.viewer, 1)

        tr = QWidget(objectName="transport")
        tl = QHBoxLayout(tr)
        tl.setContentsMargins(10, 4, 10, 4)
        for text, slot in (("|◀", self.prev_clip), ("◀◀", lambda: self.nudge(-5)), ("▶", self.toggle_play),
                           ("▶▶", lambda: self.nudge(5)), ("▶|", self.next_clip)):
            b = self.button(text, slot)
            b.setObjectName("icon")
            tl.addWidget(b)
            if text == "▶":
                self.play_btn = b
        self.time_label = QLabel(objectName="time")
        tl.addSpacing(12)
        tl.addWidget(self.time_label)
        tl.addStretch()
        self.src_btn = self.button("Recording", lambda: self.set_mode("source"), checkable=True)
        self.seq_btn = self.button("Timeline", lambda: self.set_mode("timeline"), checkable=True)
        tl.addWidget(QLabel("Preview:"))
        tl.addWidget(self.src_btn)
        tl.addWidget(self.seq_btn)
        root.addWidget(tr)

        self.rec = RecordingLane(self)
        self.seq = SequenceView(self)
        root.addWidget(QLabel("RECORDING  ·  green = keep, red = cut  ·  drag edges to adjust, drag across red to add",
                              objectName="lane"))
        root.addWidget(self.rec)
        root.addWidget(QLabel("TIMELINE  ·  drag to reorder, drag edges to trim", objectName="lane"))
        root.addWidget(self.seq)
        self.setCentralWidget(central)

        self.moments_panel = MomentsPanel(self)
        self.dock = QDockWidget("Moments", self, objectName="moments")
        self.dock.setWidget(self.moments_panel)
        self.dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.dock.visibilityChanged.connect(lambda v: self.moments_btn.setChecked(v))
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.resizeDocks([self.dock], [300], Qt.Horizontal)

        self.status_label = QLabel()
        self.progress = QProgressBar(maximum=1000, fixedWidth=220, textVisible=False, visible=False)
        self.cancel_btn = self.button("Cancel export", self.cancel_export)
        self.cancel_btn.setVisible(False)
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_btn)
        for w in self.findChildren(QAbstractButton) + self.findChildren(QSlider):
            w.setFocusPolicy(Qt.NoFocus)  # keep Space/arrows for the shortcuts

    def button(self, text, slot, checkable=False):
        b = QPushButton(text, checkable=checkable)
        b.clicked.connect(slot)
        return b

    def build_actions(self):
        menus = {
            "&File": [
                ("open", "Open recording or project...", "Ctrl+O", self.open_dialog),
                ("save", "Save project", "Ctrl+S", self.save),
                ("save_as", "Save project as...", "Ctrl+Shift+S", self.save_as),
                ("add_music", "Add music...", "Ctrl+M", self.add_music_dialog),
                ("export", "Export video...", "Ctrl+E", self.export_dialog),
                None,
                ("quit", "Quit", "Ctrl+Q", self.close)],
            "&Edit": [
                ("undo", "Undo", "Ctrl+Z", self.undo),
                ("redo", "Redo", "Ctrl+Shift+Z", self.redo),
                None,
                ("split", "Split at playhead", "S", self.split),
                ("delete", "Delete selected (ripple)", "Del", self.delete_selected),
                ("accept", "Accept clip", "A", self.accept_current),
                ("accept_all", "Accept all AI clips", "Shift+A", self.accept_all),
                None,
                ("mark_in", "Mark in (recording)", "I", lambda: self.mark(0)),
                ("mark_out", "Mark out (recording)", "O", lambda: self.mark(1)),
                ("insert", "Add marked range to edit", "E", self.insert_marked)],
            "&Playback": [
                ("play", "Play / pause", "Space", self.toggle_play),
                ("rewind", "Back 5 s (J)", "J", lambda: self.nudge(-5)),
                ("stop", "Pause (K)", "K", self.pause),
                ("faster", "Play / faster (L)", "L", self.play_faster),
                ("frame_back", "Previous frame", "Left", lambda: self.step(-FRAME)),
                ("frame_fwd", "Next frame", "Right", lambda: self.step(FRAME)),
                ("sec_back", "Back 1 s", "Shift+Left", lambda: self.step(-1)),
                ("sec_fwd", "Forward 1 s", "Shift+Right", lambda: self.step(1)),
                ("prev_clip", "Previous clip", "Up", self.prev_clip),
                ("next_clip", "Next clip", "Down", self.next_clip),
                ("home", "Go to start", "Home", lambda: self.jump(0)),
                ("end", "Go to end", "End", lambda: self.jump(1e9)),
                ("toggle_mode", "Switch recording / timeline preview", "Tab", self.toggle_mode)],
            "&View": [
                ("zoom_in", "Zoom in", "=", lambda: self.zoom(1.5)),
                ("zoom_out", "Zoom out", "-", lambda: self.zoom(1 / 1.5)),
                ("fit", "Fit timelines", "F", self.fit),
                ("snap", "Toggle snapping", "N", self.toggle_snap),
                ("moments", "Show / hide Moments panel", "M", lambda: self.dock.setVisible(not self.dock.isVisible()))],
            "&Settings": [("shortcuts", "Keyboard shortcuts...", "Ctrl+K", self.shortcut_dialog)],
        }
        try:
            with open(SHORTCUTS_FILE) as f:
                custom = json.load(f)
        except (OSError, ValueError):
            custom = {}
        self.actions, self.defaults = {}, {}
        for title, items in menus.items():
            menu = self.menuBar().addMenu(title)
            for item in items:
                if item is None:
                    menu.addSeparator()
                    continue
                name, label, key, slot = item
                a = QAction(label, self)
                a.setShortcut(QKeySequence(custom.get(name, key)))
                a.triggered.connect(slot)
                menu.addAction(a)
                self.addAction(a)
                self.actions[name], self.defaults[name] = a, key

    # ---------- project ----------
    def state(self):
        return {"clips": self.project["clips"], "music": self.project["music"]}

    def checkpoint(self):
        self.undo_stack = (self.undo_stack + [copy.deepcopy(self.state())])[-200:]
        self.redo_stack.clear()
        self.dirty = True

    def begin_edit(self):
        self._edit_snap = copy.deepcopy(self.state())

    def end_edit(self):
        if self._edit_snap is not None and self._edit_snap != self.state():
            self.undo_stack = (self.undo_stack + [self._edit_snap])[-200:]
            self.redo_stack.clear()
            self.dirty = True
        self._edit_snap = None
        self.refresh()

    def undo(self):
        if self.project and self.undo_stack:
            self.redo_stack.append(copy.deepcopy(self.state()))
            self.project.update(self.undo_stack.pop())
            self.dirty = True
            self.refresh()

    def redo(self):
        if self.project and self.redo_stack:
            self.undo_stack.append(copy.deepcopy(self.state()))
            self.project.update(self.redo_stack.pop())
            self.dirty = True
            self.refresh()

    def open_dialog(self):
        exts = " ".join("*" + e for e in VIDEO_EXT)
        path, _ = QFileDialog.getOpenFileName(self, "Open", "", f"Recordings and projects (*.aeproj {exts})")
        if path:
            self.open_path(path)

    def open_path(self, path):
        if not self.confirm_discard():
            return
        if path.lower().endswith(".aeproj"):
            with open(path) as f:
                project = json.load(f)
            if not os.path.exists(project["source"]):
                QMessageBox.warning(self, "Recording missing", f"Can't find {project['source']}")
                return
            self.path = path
        else:
            project = {"version": 1, "source": path, "keep_percent": self.keep.value(), "clips": [], "music": []}
            self.path = None
        self.project, self.dirty = project, False
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.keep.setValue(project["keep_percent"])
        self.src_t = self.seq_t = 0.0
        self.cur, self.selected, self.marks = 0, None, [None, None]
        self.duration = ae.duration_of(project["source"])
        self.player.setSource(QUrl.fromLocalFile(project["source"]))
        self.player.pause()  # shows the first frame
        self.viewer.setCurrentIndex(1)
        self.set_mode("source" if not project["clips"] else "timeline")
        self.start_analysis()
        QTimer.singleShot(0, self.fit)

    def start_analysis(self):
        self.score, self.analyzing = None, 0.0
        src = self.project["source"]

        def done(score):
            if self.project and self.project["source"] == src:
                self.score, self.analyzing = score, None
                # fresh project: fill in the AI suggestions; reopened one: just count the moments
                self.resuggest_ai(apply=not self.project["clips"], fresh=not self.project["clips"])
                self.fit()

        def failed(msg):
            self.analyzing = None
            QMessageBox.warning(self, "AI analysis failed", msg)
            self.refresh()

        self.jobs.append(in_thread(lambda prog: ae.analyze(src, prog),
                                   lambda p: (setattr(self, "analyzing", p), self.refresh()), done, failed))

    def save(self):
        if not self.project:
            return
        if not self.path:
            return self.save_as()
        self.project["keep_percent"] = self.keep.value()
        with open(self.path, "w") as f:
            json.dump(self.project, f, indent=1)
        self.dirty = False
        self.refresh()

    def save_as(self):
        if not self.project:
            return
        default = os.path.splitext(self.project["source"])[0] + ".aeproj"
        path, _ = QFileDialog.getSaveFileName(self, "Save project", self.path or default, "Project (*.aeproj)")
        if path:
            self.path = path
            self.save()

    def autosave(self):
        if self.project and self.dirty:
            target = (self.path or os.path.join(ae.CACHE_DIR, "untitled.aeproj")) + ".autosave"
            with open(target, "w") as f:
                json.dump(self.project, f, indent=1)

    def confirm_discard(self):
        if not (self.project and self.dirty):
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes to the current project?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            self.save()
            return not self.dirty
        return r == QMessageBox.Discard

    def closeEvent(self, e):
        if self.confirm_discard():
            e.accept()
        else:
            e.ignore()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(AUDIO_EXT):
                self.add_music(path)
            elif path.lower().endswith(VIDEO_EXT + (".aeproj",)):
                self.open_path(path)
                return

    # ---------- edits ----------
    def clips(self):
        return self.project["clips"] if self.project else []

    def find(self, id_):
        return next((x for x in self.clips() + (self.project["music"] if self.project else []) if x["id"] == id_), None)

    def current_clip(self):
        """Selected clip, else the clip under the playhead."""
        c = self.find(self.selected)
        if c and c in self.clips():
            return c
        if not self.clips():
            return None
        if self.mode == "timeline":
            return self.clips()[ae.locate(self.clips(), self.seq_t)[0]]
        return next((c for c in self.clips() if c["in"] <= self.src_t < c["out"]), None)

    def add_clip(self, a, b, status="manual"):
        self.checkpoint()
        c = ae.new_clip(a, b, status)
        ae.insert_by_source(self.project["clips"], c)
        self.selected = c["id"]
        self.refresh()

    def remove_clip(self, c):
        self.checkpoint()
        self.project["clips"].remove(c)
        self.selected = None
        self.refresh()

    def remove_music(self, m):
        self.checkpoint()
        self.project["music"].remove(m)
        self.selected = None
        self.refresh()

    def delete_selected(self):
        item = self.find(self.selected)
        if item in self.clips():
            self.remove_clip(item)
        elif item:
            self.remove_music(item)

    def move_clip(self, i, j):
        if i != j:
            self.checkpoint()
            self.project["clips"].insert(j, self.project["clips"].pop(i))
            self.refresh()

    def set_status(self, c, status):
        if c["status"] != status:
            self.checkpoint()
            c["status"] = status
            self.refresh()

    def accept_current(self):
        c = self.current_clip()
        if c:
            self.set_status(c, "accepted")

    def accept_all(self):
        if any(c["status"] == "suggested" for c in self.clips()):
            self.checkpoint()
            for c in self.clips():
                c["status"] = "accepted" if c["status"] == "suggested" else c["status"]
            self.refresh()

    def split(self):
        if not self.clips():
            return
        if self.mode == "timeline":
            i, s = ae.locate(self.clips(), self.seq_t)
        else:
            c = self.current_clip()
            if not c:
                return
            i, s = self.clips().index(c), self.src_t
        snap = copy.deepcopy(self.state())
        if ae.split_clip(self.project["clips"], i, s):
            self.undo_stack.append(snap)
            self.redo_stack.clear()
            self.dirty = True
            self.refresh()

    def resuggest_ai(self, apply=True, fresh=False):
        """Recompute AI suggestions (fights + moments) off the UI thread, then swap out untouched suggestions."""
        if self.score is None or not self.project:
            return
        self.ai_gen += 1
        gen, src, score, dur = self.ai_gen, self.project["source"], self.score, self.duration
        keep, moments, fights = self.keep.value(), copy.deepcopy(self.moments), self.fights_on
        self.statusBar().showMessage("AI is updating suggestions... (first time takes a few seconds)")

        def done(result):
            if gen != self.ai_gen or not self.project or self.project["source"] != src:
                return  # a newer request or another recording superseded this one
            ranges, self.found = result
            self.statusBar().clearMessage()
            self.moments_panel.refresh()
            if apply:
                clips = ae.resuggest(self.clips(), ranges)
                if clips != self.clips():
                    if not fresh:
                        self.checkpoint()
                    self.project["clips"] = clips
                self.project["keep_percent"] = keep
            if fresh:
                self.set_mode("timeline")
                self.seek_seq(0)
            self.refresh()

        def failed(msg):
            self.statusBar().clearMessage()
            QMessageBox.warning(self, "AI suggestions failed", msg)

        self.jobs.append(in_thread(lambda _: ae.ai_ranges(src, score, dur, keep, moments, fights),
                                   lambda p: None, done, failed))

    def save_moments(self):
        with open(MOMENTS_FILE, "w") as f:
            json.dump({"fights": self.fights_on, "moments": self.moments}, f, indent=1)
        self.keep.setEnabled(self.fights_on)
        self.moments_panel.refresh()
        self.resuggest_ai()

    def set_fights(self, on):
        self.fights_on = on
        self.save_moments()

    def edit_moment(self, i=None):
        m = MomentDialog.ask(self, self.moments[i] if i is not None else None)
        if m is None:
            return
        if i is None:
            self.moments.append(m)
            self.found.append(None)
        else:
            self.moments[i] = m
        self.save_moments()

    def delete_moment(self, i):
        if QMessageBox.question(self, "Delete moment", f"Delete \"{self.moments[i]['name']}\"?") == QMessageBox.Yes:
            del self.moments[i]
            del self.found[i]
            self.save_moments()

    def toggle_moment(self, i, on):
        self.moments[i]["enabled"] = on
        self.save_moments()

    def mark(self, which):
        if self.project:
            self.marks[which] = self.src_t
            self.refresh()

    def insert_marked(self):
        a, b = self.marks
        if a is not None and b is not None and b - a >= 0.5:
            self.add_clip(a, b)
            self.marks = [None, None]
            self.refresh()

    def ask_volume(self, item):
        v, ok = QInputDialog.getInt(self, "Volume", "Volume (%)", int(item["volume"] * 100), 0, 300, 5)
        if ok:
            self.checkpoint()
            item["volume"] = v / 100
            self.refresh()

    def music_length(self, path):
        if path not in self.lengths:
            self.lengths[path] = ae.duration_of(path)
        return self.lengths[path]

    def add_music_dialog(self):
        if not self.project:
            return
        exts = " ".join("*" + e for e in AUDIO_EXT)
        path, _ = QFileDialog.getOpenFileName(self, "Add music", "", f"Audio ({exts})")
        if path:
            self.add_music(path)

    def add_music(self, path):
        if not self.project:
            return
        try:
            length = self.music_length(path)
        except Exception:
            QMessageBox.warning(self, "Can't read file", path)
            return
        self.checkpoint()
        m = {"id": uuid.uuid4().hex[:8], "path": path, "start": self.seq_t, "in": 0.0, "out": length,
             "volume": 0.3, "fade_in": 1.0, "fade_out": 2.0}
        self.project["music"].append(m)
        self.selected = m["id"]
        self.refresh()

    # ---------- playback ----------
    def playing(self):
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def set_mode(self, mode):
        self.mode = mode
        self.src_btn.setChecked(mode == "source")
        self.seq_btn.setChecked(mode == "timeline")
        if mode == "source":
            self.music_player.pause()
            self.audio.setVolume(1.0)
        self.refresh()

    def toggle_mode(self):
        if self.mode == "source":
            self.seek_seq(self.seq_t)
        else:
            self.seek_source(self.src_t)

    def set_pos(self, s):
        self.player.setPosition(int(s * 1000))
        self.seek_guard = time.monotonic() + 0.3

    def seek_source(self, t, keep_mode=False):
        if not self.project:
            return
        if not keep_mode:
            self.set_mode("source")
        self.src_t = min(max(t, 0.0), self.duration)
        self.set_pos(self.src_t)
        self.after_seek()

    def seek_seq(self, t):
        if not self.project:
            return
        self.set_mode("timeline")
        if not self.clips():
            self.seq_t = 0.0
            self.refresh()
            return
        self.seq_t = min(max(t, 0.0), ae.seq_length(self.clips()))
        self.cur, self.src_t = ae.locate(self.clips(), self.seq_t)
        self.set_pos(self.src_t)
        self.sync_music()
        self.after_seek()

    def after_seek(self):
        self.rec.follow()
        self.seq.follow()
        self.refresh()

    def jump(self, t):
        self.seek_source(t) if self.mode == "source" else self.seek_seq(t)

    def step(self, dt):
        self.pause()
        self.jump((self.src_t if self.mode == "source" else self.seq_t) + dt)

    def nudge(self, dt):
        self.jump((self.src_t if self.mode == "source" else self.seq_t) + dt)

    def prev_clip(self):
        st = ae.starts(self.clips())
        self.seek_seq(max([s for s in st if s < self.seq_t - 0.2], default=0.0))

    def next_clip(self):
        st = ae.starts(self.clips())
        nxt = [s for s in st if s > self.seq_t + 0.01]
        if nxt:
            self.seek_seq(nxt[0])

    def toggle_play(self):
        if not self.project:
            return
        if self.playing():
            self.pause()
            return
        if self.mode == "timeline":
            if not self.clips():
                return
            if self.seq_t >= ae.seq_length(self.clips()) - 0.05:
                self.seq_t = 0.0
            self.seek_seq(self.seq_t)
        self.player.setPlaybackRate(1.0)
        self.player.play()
        self.play_btn.setText("⏸")

    def pause(self):
        self.player.pause()
        self.music_player.pause()
        self.player.setPlaybackRate(1.0)
        self.play_btn.setText("▶")

    def play_faster(self):
        if not self.playing():
            self.toggle_play()
        else:
            self.player.setPlaybackRate({1.0: 2.0, 2.0: 4.0}.get(self.player.playbackRate(), 1.0))

    def tick(self):
        if not self.project or not self.playing():
            return
        pos = self.player.position() / 1000
        settling = time.monotonic() < self.seek_guard  # position() lags right after a seek
        if self.mode == "source":
            if not settling:
                self.src_t = pos
        else:
            clips = self.clips()
            if not clips:
                return self.pause()
            self.cur = min(self.cur, len(clips) - 1)
            c = clips[self.cur]
            if not settling and pos >= c["out"] - 0.02:
                if self.cur + 1 >= len(clips):
                    self.seq_t = ae.seq_length(clips)
                    self.pause()
                    return self.after_seek()
                self.cur += 1
                c = clips[self.cur]
                self.set_pos(c["in"])
                pos = c["in"]
            pos = min(max(pos, c["in"]), c["out"]) if not settling else self.src_t
            self.src_t = pos
            self.seq_t = ae.starts(clips)[self.cur] + pos - c["in"]
            self.audio.setVolume(min(c["volume"], 1.0))
            self.sync_music()
        self.rec.follow()
        self.seq.follow()
        self.update_time()

    def sync_music(self):
        if not self.project or self.mode != "timeline":
            return
        t = self.seq_t
        m = next((m for m in self.project["music"] if m["start"] <= t < m["start"] + m["out"] - m["in"]), None)
        if not m or not self.playing():
            self.music_player.pause()
            self.music_cur = None if not m else self.music_cur
            return
        target = m["in"] + t - m["start"]
        if self.music_cur != m["id"]:
            if self.music_player.source() != QUrl.fromLocalFile(m["path"]):
                self.music_player.setSource(QUrl.fromLocalFile(m["path"]))
            self.music_player.setPosition(int(target * 1000))
            self.music_cur = m["id"]
        elif abs(self.music_player.position() / 1000 - target) > 0.3:
            self.music_player.setPosition(int(target * 1000))
        if self.music_player.playbackState() != QMediaPlayer.PlayingState:
            self.music_player.play()
        end = m["start"] + m["out"] - m["in"]
        env = min(1.0, (t - m["start"]) / m["fade_in"] if m["fade_in"] else 1.0,
                  (end - t) / m["fade_out"] if m["fade_out"] else 1.0)
        self.music_audio.setVolume(min(m["volume"] * env, 1.0))

    # ---------- view ----------
    def zoom(self, f):
        self.rec.zoom(f) if self.mode == "source" else self.seq.zoom(f)

    def fit(self):
        self.rec.fit()
        self.seq.fit()

    def toggle_snap(self):
        self.snapping = not self.snapping
        self.snap_btn.setChecked(self.snapping)

    def update_time(self):
        if not self.project:
            self.time_label.setText("")
            return
        if self.mode == "source":
            self.time_label.setText(f"REC  {ae.fmt(self.src_t)} / {ae.fmt(self.duration)}")
        else:
            self.time_label.setText(f"EDIT  {ae.fmt(self.seq_t)} / {ae.fmt(ae.seq_length(self.clips()))}")

    def refresh(self):
        self.rec.update()
        self.seq.update()
        self.update_time()
        name = os.path.basename(self.path or (self.project["source"] if self.project else ""))
        self.setWindowTitle(f"Auto Editor{' - ' + name if name else ''}{' *' if self.dirty else ''}")
        if not self.project:
            self.status_label.setText("Open a recording to start.")
            return
        clips = self.clips()
        kept = ae.seq_length(clips)
        review = sum(c["status"] == "suggested" for c in clips)
        msg = (f"{len(clips)} clips  ·  {ae.fmt(kept)} kept of {ae.fmt(self.duration)} "
               f"({100 * kept / max(self.duration, 1):.0f}%)  ·  {review} AI clips to review")
        if self.analyzing is not None:
            msg += f"  ·  AI analysing {self.analyzing:.0%}"
        self.status_label.setText(msg)

    # ---------- shortcuts ----------
    def shortcut_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard shortcuts")
        dlg.resize(520, 640)
        lay = QVBoxLayout(dlg)
        table = QTableWidget(len(self.actions), 2)
        table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        edits = {}
        for row, (name, a) in enumerate(self.actions.items()):
            table.setCellWidget(row, 0, QLabel("  " + a.text()))
            edits[name] = QKeySequenceEdit(a.shortcut())
            table.setCellWidget(row, 1, edits[name])
        table.resizeColumnToContents(0)
        lay.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            lambda: [e.setKeySequence(QKeySequence(self.defaults[n])) for n, e in edits.items()])
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        keys = {n: e.keySequence().toString() for n, e in edits.items()}
        used = [k for k in keys.values() if k]
        dupes = sorted({k for k in used if used.count(k) > 1})
        if dupes:
            QMessageBox.warning(self, "Shortcut conflict", "Used more than once: " + ", ".join(dupes))
            return self.shortcut_dialog()
        for n, k in keys.items():
            self.actions[n].setShortcut(QKeySequence(k))
        with open(SHORTCUTS_FILE, "w") as f:
            json.dump({n: k for n, k in keys.items() if k != QKeySequence(self.defaults[n]).toString()}, f, indent=1)

    # ---------- export ----------
    def export_dialog(self):
        if not self.clips():
            QMessageBox.information(self, "Export", "The timeline is empty.")
            return
        if self.exporting:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Export video")
        form = QFormLayout(dlg)
        out = QLineEdit(os.path.splitext(self.path or self.project["source"])[0] + "_edit.mp4")
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda: out.setText(
            QFileDialog.getSaveFileName(dlg, "Export to", out.text(), "MP4 (*.mp4)")[0] or out.text()))
        row = QHBoxLayout()
        row.addWidget(out, 1)
        row.addWidget(browse)
        form.addRow("Save to", row)
        res = QComboBox()
        for label, h in (("Same as recording", None), ("1080p", 1080), ("720p", 720)):
            res.addItem(label, h)
        fps = QComboBox()
        for f in (60, 30):
            fps.addItem(f"{f} fps", f)
        size = QComboBox()
        for label, key in (("Standard (YouTube-ready)", "standard"), ("High", "high"), ("Small", "small")):
            size.addItem(label, key)
        enc = QComboBox()
        enc.addItem("AMD GPU (fast)", "h264_amf")
        enc.addItem("CPU x264 (slower, most compatible)", "libx264")
        estimate = QLabel(objectName="muted")
        form.addRow("Resolution", res)
        form.addRow("Frame rate", fps)
        form.addRow("File size", size)
        form.addRow("Encoder", enc)
        form.addRow("Estimated size", estimate)
        kept = ae.seq_length(self.clips())
        _, src_h, _ = ae.probe_video(self.project["source"])

        def update():
            mb = kept * (ae.video_mbps(size.currentData(), res.currentData() or src_h, fps.currentData()) + 0.2) / 8
            estimate.setText(f"about {mb / 1000:.1f} GB" if mb >= 1000 else f"about {mb:.0f} MB")

        for w in (res, fps, size):
            w.currentIndexChanged.connect(update)
        update()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Export")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        path = out.text()
        settings = {"height": res.currentData(), "fps": fps.currentData(),
                    "size": size.currentData(), "encoder": enc.currentData()}
        project = copy.deepcopy(self.project)
        self.exporting = {"cancel": False}
        cancel = self.exporting

        def finish(_):
            self.export_ui(False)
            self.statusBar().showMessage(f"Exported {path}", 10_000)
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

        def failed(msg):
            self.export_ui(False)
            QMessageBox.warning(self, "Export failed", msg)

        self.export_ui(True)
        self.jobs.append(in_thread(lambda prog: ae.render(project, path, settings, prog, lambda: cancel["cancel"]),
                                   lambda p: self.progress.setValue(int(p * 1000)), finish, failed))

    def export_ui(self, on):
        self.progress.setValue(0)
        self.progress.setVisible(on)
        self.cancel_btn.setVisible(on)
        if not on:
            self.exporting = None

    def cancel_export(self):
        if self.exporting:
            self.exporting["cancel"] = True


def dark_palette(app):
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)  # dark Windows title bar
    pal = QPalette()
    for role, color in ((QPalette.Window, "#0e0f11"), (QPalette.WindowText, "#e6e6e6"), (QPalette.Base, "#16181b"),
                        (QPalette.AlternateBase, "#1b1e23"), (QPalette.Text, "#e6e6e6"), (QPalette.Button, "#1b1e23"),
                        (QPalette.ButtonText, "#e6e6e6"), (QPalette.Highlight, "#8b5cf6"),
                        (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#16181b"),
                        (QPalette.ToolTipText, "#e6e6e6")):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def main():
    app = QApplication(sys.argv)
    dark_palette(app)
    win = Editor()
    win.show()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        win.open_path(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
