#!/usr/bin/env python3
"""Platinum 3DS Builder - a small window around builder/p3ds_build.sh.

On Windows it runs the build inside WSL2 (wsl.exe); on Linux it runs bash directly.
Package as an .exe with build_exe.bat (PyInstaller). Standard library only.
"""
import base64, json, os, shlex, subprocess, sys, threading, queue

IS_WIN = os.name == "nt"
HERE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
SETTINGS = os.path.join(os.path.expanduser("~"), ".p3ds_builder.json")
FIELDS = ["repo", "platinum", "soulsilver", "sd"]
FRIENDLY = {"check": "Checking setup", "setup": "Installing tools", "link": "Linking project folder",
            "stage": "Verifying ROMs", "pspoke": "Preparing pspoke", "full": "Full build (long)",
            "patches": "Applying your patches", "relink": "Compiling + linking",
            "followers": "Following-Pokemon data", "deploy": "Copying to SD card"}

# name -> (needs root, steps)
PIPELINES = {
    "check":   [(False, ["check"])],
    "install": [(True, ["setup", "link"])],
    "full":    [(False, ["check", "stage", "pspoke"]), (True, ["link"]), (False, ["full", "patches", "relink", "followers", "deploy"])],
    "quick":   [(False, ["patches", "relink", "deploy"])],
}


def sq(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def path_expr(p):
    """Shell expression giving the Linux path for a user-entered path (Windows paths via wslpath)."""
    if not p:
        return "''"
    if IS_WIN and (len(p) > 1 and p[1] == ":" or p.startswith("\\\\")):
        return '"$(wslpath -a -u %s)"' % sq(p)
    return sq(p)


def build_command(cfg, root, steps, legacy=False):
    """argv that runs `steps` of p3ds_build.sh; `root` runs it as root (WSL: -u root)."""
    exports = ["export P3DS_ROOT=" + path_expr(cfg["repo"])]
    for key, var in (("platinum", "P3DS_PLATINUM"), ("soulsilver", "P3DS_SOULSILVER"), ("sd", "P3DS_SD")):
        if cfg.get(key):
            exports.append("export %s=%s" % (var, path_expr(cfg[key])))
    if legacy:
        exports.append("export P3DS_LEGACY=1")
    script = "; ".join(exports) + '; exec bash ' + path_expr(HERE + os.sep + "p3ds_build.sh") + " " + " ".join(steps)
    if IS_WIN:
        argv = ["wsl.exe"] + (["-u", "root"] if root else []) + ["--", "bash", "-c", script]
    else:
        argv = ["bash", "-c", script] if not root or os.geteuid() == 0 else ["sudo", "bash", "-c", script]
    return argv


def wsl_present():
    if not IS_WIN:
        return True
    try:
        r = subprocess.run(["wsl.exe", "--status"], capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False


def load_settings():
    try:
        with open(SETTINGS) as f:
            d = json.load(f)
        return {k: d.get(k, "") for k in FIELDS + ["legacy"]}
    except Exception:
        return {k: "" for k in FIELDS + ["legacy"]}


def save_settings(d):
    try:
        with open(SETTINGS, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.title("Platinum 3DS Builder")
    root.geometry("860x620")
    cfg = load_settings()
    vars_ = {k: tk.StringVar(value=cfg.get(k, "")) for k in FIELDS}
    legacy = tk.BooleanVar(value=bool(cfg.get("legacy")))
    status = tk.StringVar(value="Ready")
    q = queue.Queue()
    state = {"proc": None, "busy": False, "cancel": False}

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="x")
    rows = [("repo", "Project folder (your clone of 3ds-platinum)", "dir"),
            ("platinum", "Pokemon Platinum US Rev 1 .nds", "file"),
            ("soulsilver", "SoulSilver US .nds (optional, following Pokemon)", "file"),
            ("sd", "3DS SD card folder (optional, root of the card)", "dir")]
    for i, (key, label, kind) in enumerate(rows):
        ttk.Label(frm, text=label).grid(row=i * 2, column=0, columnspan=2, sticky="w")
        ttk.Entry(frm, textvariable=vars_[key]).grid(row=i * 2 + 1, column=0, sticky="we", padx=(0, 6), pady=(0, 6))

        def pick(k=key, kd=kind):
            p = filedialog.askdirectory() if kd == "dir" else filedialog.askopenfilename(filetypes=[("DS ROM", "*.nds"), ("All", "*.*")])
            if p:
                vars_[k].set(os.path.normpath(p) if IS_WIN else p)
        ttk.Button(frm, text="Browse...", command=pick).grid(row=i * 2 + 1, column=1, pady=(0, 6))
    frm.columnconfigure(0, weight=1)
    ttk.Checkbutton(frm, text="Also apply older patches/*.patch (advanced)", variable=legacy).grid(row=8, column=0, sticky="w")

    bar = ttk.Frame(root, padding=(10, 0))
    bar.pack(fill="x")
    buttons = []

    def add_btn(text, pipe):
        b = ttk.Button(bar, text=text, command=lambda: start(pipe))
        b.pack(side="left", padx=(0, 6), pady=4)
        buttons.append(b)
    add_btn("1. Check setup", "check")
    add_btn("2. Install tools (once)", "install")
    add_btn("3. Full build (first time)", "full")
    add_btn("Quick rebuild", "quick")
    cancel_btn = ttk.Button(bar, text="Cancel", command=lambda: cancel(), state="disabled")
    cancel_btn.pack(side="right")

    prog = ttk.Progressbar(root, mode="indeterminate")
    prog.pack(fill="x", padx=10)
    ttk.Label(root, textvariable=status).pack(anchor="w", padx=10)
    logf = ttk.Frame(root)
    logf.pack(fill="both", expand=True, padx=10, pady=8)
    log = tk.Text(logf, wrap="none", bg="#111", fg="#ddd", insertbackground="#ddd", font=("Consolas" if IS_WIN else "monospace", 9))
    sb = ttk.Scrollbar(logf, command=log.yview)
    log.configure(yscrollcommand=sb.set, state="disabled")
    sb.pack(side="right", fill="y")
    log.pack(side="left", fill="both", expand=True)
    log.tag_configure("bad", foreground="#ff7b72")
    log.tag_configure("good", foreground="#7ee787")

    def emit(text, tag=None):
        log.configure(state="normal")
        log.insert("end", text, tag)
        log.see("end")
        log.configure(state="disabled")

    def current_cfg():
        return {k: vars_[k].get().strip().strip('"') for k in FIELDS}

    def set_busy(b):
        state["busy"] = b
        for x in buttons:
            x.configure(state="disabled" if b else "normal")
        cancel_btn.configure(state="normal" if b else "disabled")
        prog.start(12) if b else prog.stop()

    def cancel():
        state["cancel"] = True
        p = state["proc"]
        if p and p.poll() is None:
            p.terminate()

    def worker(pipe, c):
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0
        ok = True
        for needs_root, steps in PIPELINES[pipe]:
            if state["cancel"]:
                ok = False
                break
            argv = build_command(c, needs_root, steps, legacy.get())
            q.put(("log", "\n$ " + " ".join(steps) + (" (as root)" if needs_root else "") + "\n", None))
            try:
                p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=creation)
            except OSError as e:
                q.put(("log", "Could not start: %s\n" % e, "bad"))
                ok = False
                break
            state["proc"] = p
            for raw in iter(p.stdout.readline, b""):
                line = raw.decode("utf-8", "replace")
                if line.startswith("##STEP:"):
                    _, name, what = line.strip().split(":", 2)
                    if what == "START":
                        q.put(("status", FRIENDLY.get(name, name) + "...", None))
                    elif what == "FAIL":
                        q.put(("log", "FAILED: %s\n" % FRIENDLY.get(name, name), "bad"))
                    continue
                if line.startswith("##DONE:"):
                    continue
                q.put(("log", line, "bad" if line.startswith(("ERROR", "  [MISSING]")) else ("good" if line.startswith("  [ok]") else None)))
            if p.wait() != 0:
                ok = False
                break
        q.put(("done", ok, None))

    def start(pipe):
        if state["busy"]:
            return
        c = current_cfg()
        problems = []
        if not c["repo"]:
            problems.append("Pick the project folder.")
        if pipe == "full" and not c["platinum"]:
            problems.append("Pick your Platinum ROM for the full build.")
        if c["repo"] and not os.path.isdir(os.path.join(c["repo"], "work_patches")) and not c["repo"].startswith("\\\\"):
            problems.append("That folder has no work_patches/ - pick your clone of 3ds-platinum.")
        if problems:
            messagebox.showwarning("Platinum 3DS Builder", "\n".join(problems))
            return
        if IS_WIN and not wsl_present():
            messagebox.showerror("WSL2 needed", "This builder runs the build in WSL2.\nOpen PowerShell as admin, run:  wsl --install\nthen restart Windows and try again.")
            return
        if c["repo"].lower().startswith(("c:", "d:", "e:")) or c["repo"].startswith("/mnt/"):
            emit("Note: the project is on a Windows drive. It works but builds slowly; a clone inside WSL (\\\\wsl$\\Ubuntu\\home\\you\\3ds-platinum) is much faster.\n")
        if pipe == "full":
            if not messagebox.askokcancel("Full build", "The full build compiles the whole game from your ROM and can take a long time. Continue?"):
                return
        d = dict(c)
        d["legacy"] = legacy.get()
        save_settings(d)
        state["cancel"] = False
        set_busy(True)
        threading.Thread(target=worker, args=(pipe, c), daemon=True).start()

    def pump():
        try:
            while True:
                kind, a, b = q.get_nowait()
                if kind == "log":
                    emit(a, b)
                elif kind == "status":
                    status.set(a)
                elif kind == "done":
                    set_busy(False)
                    status.set("Done." if a else ("Cancelled." if state["cancel"] else "Stopped - see the red lines in the log."))
                    emit("\n=== %s ===\n" % ("SUCCESS" if a else "STOPPED"), "good" if a else "bad")
        except queue.Empty:
            pass
        root.after(100, pump)

    emit("Steps: 1) Check, 2) Install tools once, 3) Full build once, then Quick rebuild after code changes.\n"
         "Your ROMs never leave this computer; nothing is uploaded.\n")
    pump()
    root.mainloop()


if __name__ == "__main__":
    run_gui()
