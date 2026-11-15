#!/usr/bin/env python3
"""
carplay_gui.py — desktop window for enable_carplay.py / verify_carplay.py.

Runs the same functions as the command-line scripts, in a background thread,
and shows their output in the window. Built into UVCarPlay.exe by
.github/workflows/build-windows.yml.
"""
import os, platform, queue, sys, threading
from pathlib import Path

# Store adb and backups in a per-user app folder, not ~/UVA. Must be set before
# the scripts are imported, because they read WORKDIR at import time.
if "WORKDIR" not in os.environ:
    base = os.environ.get("LOCALAPPDATA") if platform.system() == "Windows" else None
    os.environ["WORKDIR"] = str(Path(base) / "UVCarPlay" if base else Path.home() / ".uvcarplay")

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import enable_carplay as ec
import verify_carplay as vc

APP_TITLE = "UV CarPlay Enabler"


class QueueWriter:
    """File-like object that sends written text to the GUI's queue."""
    def __init__(self, q): self.q = q
    def write(self, s):
        if s: self.q.put(("log", s))
    def flush(self): pass


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        root.title(APP_TITLE)
        root.minsize(640, 460)

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)

        warn = ("Not an official Ultraviolette tool. Use only with the bike parked, "
                "ignition on, side stand down. The change can be undone with \"Undo\".")
        ttk.Label(frm, text=warn, wraplength=600, foreground="#a33").pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(frm); row.pack(fill="x")
        ttk.Label(row, text="Dash IP address:").pack(side="left")
        self.ip = tk.StringVar()
        ttk.Entry(row, textvariable=self.ip, width=18).pack(side="left", padx=6)
        ttk.Label(row, text="(required — see the README for how to find it)").pack(side="left")

        krow = ttk.Frame(frm); krow.pack(fill="x", pady=(6, 0))
        ttk.Label(krow, text="License key:").pack(side="left")
        self.key = tk.StringVar(value=ec.saved_key())
        ttk.Entry(krow, textvariable=self.key, width=60).pack(side="left", padx=6, fill="x", expand=True)
        ttk.Label(frm, text="Needed only for \"Enable CarPlay\". Click \"Show dash ID\" to get the ID "
                  "you enter when you buy a key.", foreground="#666").pack(anchor="w")

        btns = ttk.Frame(frm); btns.pack(fill="x", pady=10)
        self.buttons = []
        for label, fn in [
            ("Show dash ID", self.show_id),
            ("Preview change", self.preview),
            ("Enable CarPlay", self.enable),
            ("Verify: start", self.verify_start),
            ("Verify: check", self.verify_check),
            ("Undo", self.undo),
        ]:
            b = ttk.Button(btns, text=label, command=fn)
            b.pack(side="left", padx=(0, 6))
            self.buttons.append(b)

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(frm, textvariable=self.status).pack(anchor="w")

        self.out = scrolledtext.ScrolledText(frm, height=18, state="disabled",
                                             font=("Consolas", 10) if platform.system() == "Windows" else None)
        self.out.pack(fill="both", expand=True, pady=(6, 0))

        ttk.Label(frm, text=f"Backups and adb are stored in: {os.environ['WORKDIR']}",
                  foreground="#666").pack(anchor="w", pady=(6, 0))

        root.after(100, self.drain)

    # ----- output ------------------------------------------------------------
    def drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.out.configure(state="normal")
                    self.out.insert("end", val)
                    self.out.see("end")
                    self.out.configure(state="disabled")
                elif kind == "done":
                    self.set_busy(False, val)
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def set_busy(self, busy, msg):
        self.busy = busy
        self.status.set(msg)
        for b in self.buttons:
            b.configure(state="disabled" if busy else "normal")

    # ----- running an action -------------------------------------------------
    def start(self, name, module, action):
        """Connect with `module`, then run action() in a background thread."""
        if self.busy:
            return
        ip = self.ip.get().strip() or None
        if not ip:
            messagebox.showinfo(APP_TITLE,
                "Enter the dash IP address in the box first.\n\n"
                "See the README for how to find your dash's IP address.")
            return
        self.set_busy(True, f"{name}: running...")
        self.q.put(("log", f"\n===== {name} =====\n"))

        def work():
            w = QueueWriter(self.q)
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = w
            result = f"{name}: finished."
            try:
                module.ensure_adb()
                module.connect_dash(ip)
                action(module)
            except SystemExit as e:
                # the scripts call sys.exit(1) on errors; verify --check exits 0 on PASS/PARTIAL
                if e.code not in (0, None):
                    result = f"{name}: failed. See the log above."
            except Exception as e:
                print(f"ERROR: {type(e).__name__}: {e}")
                result = f"{name}: failed. See the log above."
            finally:
                sys.stdout, sys.stderr = old_out, old_err
                self.q.put(("done", result))

        threading.Thread(target=work, daemon=True).start()

    def show_id(self):
        self.start("Show dash ID", ec, lambda m: print(f"Your dash ID: {m.read_dash_id()}"))

    def preview(self):
        self.start("Preview", ec, lambda m: m.do_apply("dry-run", False))

    def enable(self):
        if messagebox.askokcancel(APP_TITLE,
                "Enable CarPlay now?\n\nThe dash screen goes blank for a few seconds "
                "while its UI restarts. A backup is saved first."):
            key = self.key.get().strip()   # read here: Tk is not thread-safe
            self.start("Enable CarPlay", ec, lambda m: m.do_apply("apply", False, key))

    def verify_start(self):
        ip = self.ip.get().strip() or None   # read here: Tk is not thread-safe
        def action(m):
            m.do_start(ip)
            print("In this app: after the steps above, turn the dash Wi-Fi back on,\n"
                  "then click \"Verify: check\" instead of running step 6.")
        self.start("Verify: start", vc, action)

    def verify_check(self):
        self.start("Verify: check", vc, lambda m: m.do_check())

    def undo(self):
        if messagebox.askokcancel(APP_TITLE,
                "Restore the most recent backup to the dash?\n\n"
                "The dash UI restarts."):
            self.start("Undo", ec, lambda m: m.do_restore())


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
