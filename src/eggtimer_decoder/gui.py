"""Desktop window (Qt 6 via PySide6). All logic lives in ``gui_model``; this is widgets only.

Qt follows the desktop's scale (Wayland fractional scaling, Windows per-monitor DPI) by itself.

Threading: receiver events arrive on the receiver's delivery thread and are only queued
(``Controller.events``); the Qt thread drains the queue every ``POLL_MS`` with a ``QTimer``.
Device discovery also runs on a helper thread and reports back through a queue.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import sdr
from .channels import NUM_CHANNELS
from .contracts import Event, Receiver, ReceiverState
from .gui_model import (
    Controller,
    FormValues,
    StatusModel,
    device_choices,
    event_to_row,
    parse_form,
    settings_to_form,
)
from .receiver import LiveReceiver
from .settings import Settings, load_settings
from .sinks import LastPositionStore

__all__ = ["App", "main"]

POLL_MS = 50
MAX_ROWS = 1000
AUTOCLOSE_ENV = "EGGTIMER_GUI_AUTOCLOSE_MS"
STATUS_ROWS = (
    ("state", "Receiver"),
    ("signal", "Signal"),
    ("offset", "Carrier offset"),
    ("position", "Position"),
    ("counts", "Decoded"),
)


class App(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        receiver_factory: Callable[[Settings], Receiver] = LiveReceiver,
        path: Path | None = None,
        discover: Callable[[], list] = sdr.discover,
        autostart: bool = True,
        warning: str | None = None,
    ) -> None:
        super().__init__()
        self.store = LastPositionStore(settings.position_file)
        self.controller = Controller(settings, receiver_factory, path, [self.store])
        self.status = StatusModel(position=self.store.position, position_time=self.store.timestamp)
        self.choices = device_choices()
        self._discover = discover
        self._found: queue.Queue = queue.Queue()
        self._closed = False
        self._build()
        self._load_form(settings)
        self._render_status()
        if warning:
            self._show_error(warning)
        self._refresh_devices()
        if autostart:
            self.controller.start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_MS)

    # -- layout ------------------------------------------------------
    def _build(self) -> None:
        self.setWindowTitle("Eggtimer decoder")
        self.resize(900, 640)
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        form = QGroupBox("Settings (apply any time, also while receiving)")
        g = QGridLayout(form)
        self.device_box = QComboBox()
        self.device_box.setEditable(True)
        self.freq_edit = QLineEdit()
        self.channel_box = QComboBox()
        self.channel_box.addItems([str(i) for i in range(NUM_CHANNELS)])
        self.gain_edit = QLineEdit()
        self.ppm_edit = QLineEdit()
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self._refresh_devices)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.apply)
        self.run_btn = QPushButton("Stop")
        self.run_btn.clicked.connect(self.toggle)
        g.addWidget(QLabel("Device"), 0, 0)
        g.addWidget(self.device_box, 0, 1, 1, 3)
        g.addWidget(rescan, 0, 4)
        g.addWidget(QLabel("Center (MHz)"), 1, 0)
        g.addWidget(self.freq_edit, 1, 1)
        g.addWidget(QLabel("Channel"), 1, 2, Qt.AlignRight)
        g.addWidget(self.channel_box, 1, 3)
        g.addWidget(QLabel("Gain (dB or auto)"), 2, 0)
        g.addWidget(self.gain_edit, 2, 1)
        g.addWidget(QLabel("PPM"), 2, 2, Qt.AlignRight)
        g.addWidget(self.ppm_edit, 2, 3)
        btns = QVBoxLayout()
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.run_btn)
        g.addLayout(btns, 1, 4, 2, 1)
        g.setColumnStretch(1, 1)
        outer.addWidget(form)
        # Enter applies; picking a channel applies at once (as before).
        for e in (self.freq_edit, self.gain_edit, self.ppm_edit):
            e.returnPressed.connect(self.apply)
        self.device_box.lineEdit().returnPressed.connect(self.apply)
        self.channel_box.activated.connect(lambda _i: self.apply())

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b00020")
        outer.addWidget(self.error_label)

        st = QGroupBox("Status")
        sg = QGridLayout(st)
        self.status_labels: dict[str, QLabel] = {}
        for i, (key, title) in enumerate(STATUS_ROWS):
            self.status_labels[key] = QLabel()
            self.status_labels[key].setTextInteractionFlags(Qt.TextSelectableByMouse)
            sg.addWidget(QLabel(title), i, 0)
            sg.addWidget(self.status_labels[key], i, 1)
        sg.setColumnStretch(1, 1)
        outer.addWidget(st)

        lst = QGroupBox("Packets and NMEA")
        lv = QVBoxLayout(lst)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Time", "Kind", "Text"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().resizeSection(0, 90)
        self.tree.header().resizeSection(1, 70)
        lv.addWidget(self.tree)
        outer.addWidget(lst, 1)

    # -- form --------------------------------------------------------
    def _load_form(self, s: Settings) -> None:
        f = settings_to_form(s, self.choices)
        self.device_box.setEditText(f.device)
        self.freq_edit.setText(f.freq_mhz)
        self.channel_box.setCurrentText(f.channel)
        self.gain_edit.setText(f.gain)
        self.ppm_edit.setText(f.ppm)

    def form_values(self) -> FormValues:
        return FormValues(
            device=self.device_box.currentText(),
            freq_mhz=self.freq_edit.text(),
            channel=self.channel_box.currentText(),
            gain=self.gain_edit.text(),
            ppm=self.ppm_edit.text(),
        )

    def _show_error(self, text: str) -> None:
        self.error_label.setText(text)

    def apply(self) -> None:
        """Validate the form and apply it to the running receiver; show errors inline."""
        result = parse_form(self.form_values(), self.controller.settings, self.choices)
        if result.settings is None:
            self._show_error("; ".join(result.errors.values()))
            return
        try:
            notes = self.controller.apply(result.settings)
        except (ValueError, OSError) as e:
            self._show_error(str(e))
            return
        self._show_error("")
        self._load_form(self.controller.settings)
        self.status.message = "; ".join(notes)
        self._render_status()

    def toggle(self) -> None:
        if self.controller.running:
            self.controller.stop()
        else:
            self.controller.start()

    # -- devices -----------------------------------------------------
    def _refresh_devices(self) -> None:
        def work() -> None:
            self._found.put(self._discover())

        threading.Thread(target=work, name="eggtimer-discover", daemon=True).start()

    def _set_devices(self, infos: list) -> None:
        self.choices = device_choices(infos)
        text = self.device_box.currentText()
        self.device_box.clear()
        self.device_box.addItems([label for label, _ in self.choices])
        self.device_box.setEditText(text)

    # -- event pump --------------------------------------------------
    def _poll(self) -> None:
        if self._closed:
            return
        try:
            infos = self._found.get_nowait()
        except queue.Empty:
            pass
        else:
            self._set_devices(infos)
        events = self.controller.drain()
        for ev in events:
            self._handle(ev)
        if events:
            self._render_status()

    def _handle(self, ev: Event) -> None:
        self.status.apply(ev)
        row = event_to_row(ev)
        if row is not None:
            item = QTreeWidgetItem([row.time, row.kind, row.text])
            self.tree.addTopLevelItem(item)
            self.tree.scrollToItem(item)
            extra = self.tree.topLevelItemCount() - MAX_ROWS
            for _ in range(max(extra, 0)):
                self.tree.takeTopLevelItem(0)

    def _render_status(self) -> None:
        s = self.status
        self.status_labels["state"].setText(s.state_text())
        self.status_labels["signal"].setText(s.signal_text())
        self.status_labels["offset"].setText(s.offset_text())
        self.status_labels["position"].setText(s.position_text())
        self.status_labels["counts"].setText(s.counts_text())
        self.run_btn.setText("Stop" if s.state not in (ReceiverState.STOPPED, ReceiverState.ERROR) else "Start")

    def close_app(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self.controller.close()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.close_app()
        super().closeEvent(event)


def main(*, autoclose_ms: int | None = None, path: Path | None = None) -> int:
    """Open the window. Settings come from the saved file (and EGGTIMER_* env), as for the CLI.

    ``autoclose_ms`` (or env ``EGGTIMER_GUI_AUTOCLOSE_MS``) closes the window after that long;
    used for smoke tests.
    """
    from .cli import settings_path

    path = path or settings_path()
    settings, warning = load_settings(path, os.environ)
    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    win = App(settings, path=path, warning=warning)
    if autoclose_ms is None and os.environ.get(AUTOCLOSE_ENV):
        autoclose_ms = int(os.environ[AUTOCLOSE_ENV])
    if autoclose_ms is not None:
        QTimer.singleShot(autoclose_ms, win.close)
    win.show()
    qapp.exec()
    win.close_app()
    return 0
