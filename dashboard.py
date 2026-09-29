"""Local Tkinter dashboard for sw-audit.

Buttons wrap the existing CLI scripts as subprocesses and stream their
console output live. Read-only against the store, same as the CLI.
Run with:  python dashboard.py
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

COMMANDS = [
    ("Full Audit (run_all)", [sys.executable, "-u", "run_all.py"]),
    ("Products Audit", [sys.executable, "-u", "audit.py"]),
    ("Collections Audit", [sys.executable, "-u", "collections_audit.py"]),
    ("Lint Task Configs", [sys.executable, "-u", "audit.py", "--lint"]),
]


class Dashboard:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.q = queue.Queue()
        root.title("sw-audit dashboard")
        root.geometry("900x600")

        bar = ttk.Frame(root, padding=8)
        bar.pack(fill="x")
        self.buttons = []
        for label, cmd in COMMANDS:
            b = ttk.Button(bar, text=label, command=lambda c=cmd, l=label: self.run(c, l))
            b.pack(side="left", padx=4)
            self.buttons.append(b)
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=12)
        ttk.Button(bar, text="Open Output Folder", command=self.open_output).pack(side="right", padx=4)

        self.status = tk.StringVar(value="idle")
        ttk.Label(root, textvariable=self.status, padding=(10, 0)).pack(anchor="w")

        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)
        self.log = tk.Text(frame, wrap="none", state="disabled",
                           bg="#111318", fg="#d7dae0", insertbackground="#d7dae0")
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.drain)

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def run(self, cmd, label):
        if self.proc:
            return
        self.append(f"\n=== {label} ===\n")
        self.status.set(f"running: {label}")
        for b in self.buttons:
            b.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=REPO_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace")
        except OSError as e:
            self.append(f"failed to start: {e}\n")
            self.finish(None)
            return
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()

    def reader(self, proc):
        for line in proc.stdout:
            self.q.put(line)
        proc.wait()
        self.q.put(("__done__", proc.returncode))

    def drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item[0] == "__done__":
                    self.finish(item[1])
                else:
                    self.append(item)
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def finish(self, code):
        if code is not None:
            self.append(f"=== exited with code {code} ===\n")
        self.status.set("idle")
        self.proc = None
        for b in self.buttons:
            b.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def stop(self):
        if self.proc:
            self.append("stopping... (any bulk op keeps running on Shopify; "
                        "next run resumes it)\n")
            self.proc.terminate()

    def open_output(self):
        path = os.path.join(REPO_DIR, "output")
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    def on_close(self):
        if self.proc:
            self.proc.terminate()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Dashboard(root).mainloop()