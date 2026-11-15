"""Desktop window (tkinter, standard library). All logic lives in ``gui_model``; this is widgets only.

Threading: receiver events arrive on the receiver's delivery thread and are only queued
(``Controller.events``); the Tk thread drains the queue every ``POLL_MS`` with ``after``.
Device discovery also runs on a helper thread and reports back through a queue.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

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


class App:
    def __init__(
        self,
        root: tk.Tk,
        settings: Settings,
        receiver_factory: Callable[[Settings], Receiver] = LiveReceiver,
        path: Path | None = None,
        discover: Callable[[], list] = sdr.discover,
        autostart: bool = True,
        warning: str | None = None,
    ) -> None:
        self.root = root
        self.store = LastPositionStore(settings.position_file)
        self.controller = Controller(settings, receiver_factory, path, [self.store])
        self.status = StatusModel(position=self.store.position, position_time=self.store.timestamp)
        self.choices = device_choices()
        self._discover = discover
        self._found: queue.Queue = queue.Queue()
        self._closed = False
        self._build()
        self._load_form(settings)
        if warning:
            self._show_error(warning)
        self._refresh_devices()
        if autostart:
            self.controller.start()
        self._poll()

    # -- layout ------------------------------------------------------
    def _build(self) -> None:
        root = self.root
        root.title("Eggtimer decoder")
        root.minsize(720, 480)
        self.vars = {k: tk.StringVar() for k in ("device", "freq_mhz", "channel", "gain", "ppm")}

        form = ttk.LabelFrame(root, text="Settings (apply any time, also while receiving)")
        form.pack(fill="x", padx=8, pady=6)
        ttk.Label(form, text="Device").grid(row=0, column=0, sticky="w", padx=4)
        self.device_box = ttk.Combobox(form, textvariable=self.vars["device"], width=34)
        self.device_box.grid(row=0, column=1, columnspan=3, sticky="we", padx=4, pady=2)
        ttk.Button(form, text="Rescan", command=self._refresh_devices).grid(row=0, column=4, padx=4)
        ttk.Label(form, text="Center (MHz)").grid(row=1, column=0, sticky="w", padx=4)
        ttk.Entry(form, textvariable=self.vars["freq_mhz"], width=14).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(form, text="Channel").grid(row=1, column=2, sticky="e", padx=4)
        self.channel_box = ttk.Combobox(
            form, textvariable=self.vars["channel"], values=[str(i) for i in range(NUM_CHANNELS)], width=4, state="readonly"
        )
        self.channel_box.grid(row=1, column=3, sticky="w", padx=4)
        self.channel_box.bind("<<ComboboxSelected>>", lambda _e: self.apply())
        ttk.Label(form, text="Gain (dB or auto)").grid(row=2, column=0, sticky="w", padx=4)
        ttk.Entry(form, textvariable=self.vars["gain"], width=14).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(form, text="PPM").grid(row=2, column=2, sticky="e", padx=4)
        ttk.Entry(form, textvariable=self.vars["ppm"], width=8).grid(row=2, column=3, sticky="w", padx=4)
        buttons = ttk.Frame(form)
        buttons.grid(row=1, column=4, rowspan=2, padx=4)
        self.apply_btn = ttk.Button(buttons, text="Apply", command=self.apply)
        self.apply_btn.pack(fill="x")
        self.run_btn = ttk.Button(buttons, text="Stop", command=self.toggle)
        self.run_btn.pack(fill="x", pady=2)
        form.columnconfigure(1, weight=1)
        root.bind("<Return>", lambda _e: self.apply())

        self.error_var = tk.StringVar()
        self.error_label = ttk.Label(root, textvariable=self.error_var, foreground="#b00020", wraplength=700)
        self.error_label.pack(fill="x", padx=10)

        st = ttk.LabelFrame(root, text="Status")
        st.pack(fill="x", padx=8, pady=4)
        self.status_vars = {k: tk.StringVar() for k in ("state", "signal", "offset", "position", "counts")}
        for i, (key, title) in enumerate(
            (("state", "Receiver"), ("signal", "Signal"), ("offset", "Carrier offset"), ("position", "Position"), ("counts", "Decoded"))
        ):
            ttk.Label(st, text=title).grid(row=i, column=0, sticky="w", padx=4)
            ttk.Label(st, textvariable=self.status_vars[key]).grid(row=i, column=1, sticky="w", padx=4)

        lst = ttk.LabelFrame(root, text="Packets and NMEA")
        lst.pack(fill="both", expand=True, padx=8, pady=6)
        self.tree = ttk.Treeview(lst, columns=("time", "kind", "text"), show="headings", height=10)
        for col, w in (("time", 70), ("kind", 60), ("text", 560)):
            self.tree.heading(col, text=col.title())
            self.tree.column(col, width=w, stretch=(col == "text"))
        sb = ttk.Scrollbar(lst, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        root.protocol("WM_DELETE_WINDOW", self.close)

    # -- form --------------------------------------------------------
    def _load_form(self, s: Settings) -> None:
        f = settings_to_form(s, self.choices)
        for k, v in vars(f).items():
            self.vars[k].set(v)

    def form_values(self) -> FormValues:
        return FormValues(**{k: v.get() for k, v in self.vars.items()})

    def _show_error(self, text: str) -> None:
        self.error_var.set(text)

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
        self.device_box["values"] = [label for label, _ in self.choices]

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
        self.root.after(POLL_MS, self._poll)

    def _handle(self, ev: Event) -> None:
        self.status.apply(ev)
        row = event_to_row(ev)
        if row is not None:
            iid = self.tree.insert("", "end", values=(row.time, row.kind, row.text))
            self.tree.see(iid)
            children = self.tree.get_children()
            if len(children) > MAX_ROWS:
                self.tree.delete(*children[: len(children) - MAX_ROWS])

    def _render_status(self) -> None:
        s = self.status
        self.status_vars["state"].set(s.state_text())
        self.status_vars["signal"].set(s.signal_text())
        self.status_vars["offset"].set(s.offset_text())
        self.status_vars["position"].set(s.position_text())
        self.status_vars["counts"].set(s.counts_text())
        self.run_btn.configure(text="Stop" if s.state not in (ReceiverState.STOPPED, ReceiverState.ERROR) else "Start")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.controller.close()
        self.root.destroy()


def main(*, autoclose_ms: int | None = None, path: Path | None = None) -> int:
    """Open the window. Settings come from the saved file (and EGGTIMER_* env), as for the CLI.

    ``autoclose_ms`` (or env ``EGGTIMER_GUI_AUTOCLOSE_MS``) closes the window after that long;
    used for smoke tests.
    """
    from .cli import settings_path

    path = path or settings_path()
    settings, warning = load_settings(path, os.environ)
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"cannot open a window: {e}")
        return 1
    app = App(root, settings, path=path, warning=warning)
    if autoclose_ms is None and os.environ.get(AUTOCLOSE_ENV):
        autoclose_ms = int(os.environ[AUTOCLOSE_ENV])
    if autoclose_ms is not None:
        root.after(autoclose_ms, app.close)
    root.mainloop()
    return 0
