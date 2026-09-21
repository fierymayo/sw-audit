#!/usr/bin/env python3
"""Tkinter front-end. Buttons map 1:1 to the CLI commands; all logic lives in
audit.pipeline / collections_audit.pipeline. Run: python ui.py"""
import os
import platform
import queue
import subprocess
import threading

import config


def open_output_folder():
    os.makedirs(config.OUT_DIR, exist_ok=True)
    path = os.path.abspath(config.OUT_DIR)
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def main():
    import tkinter as tk
    from tkinter import scrolledtext

    import audit
    import collections_audit

    root = tk.Tk()
    root.title("SW Catalog Audit (read-only)")
    root.geometry("860x560")

    log_queue = queue.Queue()
    running = {"flag": False}
    buttons = []

    def log(msg):
        log_queue.put(str(msg))

    def drain():
        try:
            while True:
                line = log_queue.get_nowait()
                text.configure(state="normal")
                text.insert("end", line + "\n")
                text.see("end")
                text.configure(state="disabled")
        except queue.Empty:
            pass
        root.after(100, drain)

    def set_busy(busy):
        running["flag"] = busy
        state = "disabled" if busy else "normal"
        for b in buttons:
            b.configure(state=state)
        status.configure(text="Running…" if busy else "Idle")

    def launch(label, fn, **kwargs):
        if running["flag"]:
            return

        def worker():
            log(f"=== {label} ===")
            try:
                fn(log=log, **kwargs)
            except SystemExit as e:
                log(f"STOPPED: {e}")
            except Exception as e:
                log(f"ERROR: {e}")
            finally:
                root.after(0, set_busy, False)

        set_busy(True)
        threading.Thread(target=worker, daemon=True).start()

    top = tk.Frame(root, padx=8, pady=8)
    top.pack(fill="x")

    def add_button(label, command):
        b = tk.Button(top, text=label, command=command, width=20)
        b.pack(side="left", padx=3)
        buttons.append(b)

    add_button("Run Product Audit", lambda: launch("Product audit (fetch + scan)", audit.pipeline))
    add_button("Rescan Cached", lambda: launch("Product audit (cached)", audit.pipeline, cached=True))
    add_button("Collections Audit", lambda: launch("Collections audit", collections_audit.pipeline))
    add_button("Collections + Members", lambda: launch("Collections (members + Adds)", collections_audit.pipeline, members_pull=True, deliverable=True))
    add_button("Collections XLSX (cached)", lambda: launch("Collections deliverable (cached)", collections_audit.pipeline, cached=True, deliverable=True))
    add_button("Config Lint", lambda: launch("Config lint", audit.pipeline, lint_only=True))
    add_button("PDF (cached scan)", lambda: launch("Cached scan + PDF", audit.pipeline, cached=True, pdf=True))

    tk.Button(top, text="Open Output Folder", command=open_output_folder, width=18).pack(side="right")

    text = scrolledtext.ScrolledText(root, state="disabled", font=("Consolas", 9))
    text.pack(fill="both", expand=True, padx=8, pady=4)

    status = tk.Label(root, text="Idle", anchor="w", padx=8)
    status.pack(fill="x")

    drain()
    root.mainloop()


if __name__ == "__main__":
    main()
