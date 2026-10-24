#!/usr/bin/env python3
"""Platinum 3DS Builder - a one-button window around p3ds_build.sh.

Pick your Platinum .nds and your SD card, press "Build and install". The window works out which steps are
still needed (tools, pspoke, full build), installs your patches, builds the game and copies it to the card.
Everything else lives under "Advanced".

On Windows it runs the build inside WSL2 (wsl.exe); on Linux it runs bash directly.
Package as an .exe with build_exe.bat (PyInstaller). Standard library only.
"""
import hashlib, json, os, re, string, subprocess, sys, threading, queue

IS_WIN = os.name == "nt"
HERE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
# The .exe carries its own copy of the build scripts (build_exe.bat bundles them), so it works from any folder.
SCRIPTS = getattr(sys, "_MEIPASS", HERE)
SETTINGS = os.path.join(os.path.expanduser("~"), ".p3ds_builder.json")
LAST_LOG = os.path.join(os.path.expanduser("~"), "p3ds_builder_last_log.txt")
FIELDS = ["repo", "platinum", "soulsilver", "sd"]
PLAT_SHA1 = "0862ec35b24de5c7e2dcb88c9eea0873110d755c"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0
FRIENDLY = {"check": "Checking setup", "needs": "Working out what is needed", "setup": "Installing build tools (one time)",
            "link": "Linking project folder", "stage": "Checking your game file", "pspoke": "Preparing pspoke",
            "full": "Building the whole game (first time, long)", "patches": "Installing your patches",
            "testpatches": "Testing your patches", "relink": "Compiling and linking",
            "followers": "Making following-Pokemon data", "deploy": "Copying to the SD card"}

# Advanced buttons: name -> [(needs root, steps)]. "auto" (the big button) is planned at run time.
PIPELINES = {
    "check":   [(False, ["check"])],
    "install": [(True, ["setup", "link"])],
    "test":    [(False, ["testpatches"])],
    "quick":   [(False, ["patches", "relink", "deploy"])],
    "full":    [(False, ["check", "stage", "pspoke"]), (True, ["link"]),
                (False, ["full", "patches", "relink", "followers", "deploy"])],
}

# Known failure text -> what to tell the user. First match wins; the script's own ##HINT lines win over these.
HINTS = [
    (r"No space left on device", "The disk is full. Free up about 10 GB and press the button again."),
    (r"Could not resolve host|Temporary failure in name resolution|unable to access 'https|Failed to fetch",
     "Could not download something. Check that this computer is online, then press the button again."),
    (r"Read-only file system", "The SD card is write-protected. Slide its lock switch up, plug it back in, try again."),
    (r"\$'\\r': command not found", "A script has Windows line endings. Press the button again (they are fixed automatically)."),
    (r"has no installed distributions|There is no distribution",
     "WSL has no Linux installed yet. Open PowerShell and run:  wsl --install -d Ubuntu   then restart."),
    (r"virtual memory exhausted|cc1.*: out of memory|Killed\s+signal|internal compiler error: Killed",
     "The computer ran out of memory while compiling. Close other programs and press the button again."),
    (r"sudo: a password is required", "Installing tools needs admin rights. On Linux run:  sudo bash p3ds_build.sh setup"),
]


def sq(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def wsl_unc_to_linux(p):
    r"""\\wsl$\Ubuntu\home\me\x or \\wsl.localhost\Ubuntu\home\me\x -> /home/me/x (else None)."""
    m = re.match(r"^\\\\(?:wsl\$|wsl\.localhost)\\[^\\]+(\\.*)?$", p, re.I)
    if m:
        return (m.group(1) or "\\").replace("\\", "/")
    return None


def path_expr(p):
    """Shell expression giving the Linux path for a user-entered path (Windows paths via wslpath)."""
    if not p:
        return "''"
    if IS_WIN:
        lin = wsl_unc_to_linux(p)
        if lin is not None:
            return sq(lin)
        if len(p) > 1 and p[1] == ":" or p.startswith("\\\\"):
            return '"$(wslpath -a -u %s)"' % sq(p.replace("\\", "/") if len(p) > 1 and p[1] == ":" else p)
    return sq(p)


def build_command(cfg, root, steps, legacy=False):
    """argv that runs `steps` of p3ds_build.sh; `root` runs it as root (WSL: -u root)."""
    exports = ["export P3DS_ROOT=" + path_expr(cfg["repo"])]
    for key, var in (("platinum", "P3DS_PLATINUM"), ("soulsilver", "P3DS_SOULSILVER"), ("sd", "P3DS_SD")):
        if cfg.get(key):
            exports.append("export %s=%s" % (var, path_expr(cfg[key])))
    if legacy:
        exports.append("export P3DS_LEGACY=1")
    # setsid: the build gets its own process group, so Cancel can stop make/gcc too, not just bash.
    script = "; ".join(exports) + "; exec setsid -w bash " + path_expr(os.path.join(SCRIPTS, "p3ds_build.sh")) + " " + " ".join(steps)
    if IS_WIN:
        return ["wsl.exe"] + (["-u", "root"] if root else []) + ["--", "bash", "-c", script]
    if not root or os.geteuid() == 0:
        return ["bash", "-c", script]
    return ["pkexec", "bash", "-c", script] if not sudo_ok() and which("pkexec") else ["sudo", "-n", "bash", "-c", script]


def cancel_command():
    kill = 'for f in /tmp/p3ds_builder.*.pid; do [ -f "$f" ] && kill -TERM -- -"$(cat "$f")" 2>/dev/null; rm -f "$f"; done; true'
    if IS_WIN:
        return ["wsl.exe", "-u", "root", "--", "bash", "-c", kill]
    return ["bash", "-c", kill]


def which(x):
    from shutil import which as w
    return w(x)


def sudo_ok():
    try:
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def wsl_state():
    """'ok' | 'no-wsl' | 'no-distro' (always 'ok' off Windows)."""
    if not IS_WIN:
        return "ok"
    try:
        r = subprocess.run(["wsl.exe", "-e", "true"], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    except Exception:
        return "no-wsl"
    if r.returncode == 0:
        return "ok"
    out = (r.stdout + r.stderr).replace(b"\x00", b"").decode("utf-8", "replace").lower()
    return "no-distro" if "distribution" in out else "no-wsl"


def rom_report(path, want="platinum"):
    """(ok, message) for a picked .nds. Mirrors rom_problem in p3ds_build.sh."""
    if not path:
        return None, ""
    if not os.path.isfile(path):
        return False, "File not found."
    try:
        with open(path, "rb") as f:
            head = f.read(0x200)
        code = head[12:16].decode("ascii", "replace") if len(head) >= 16 else ""
        rev = head[30] if len(head) > 30 else -1
        size = os.path.getsize(path)
    except OSError as e:
        return False, "Cannot read that file (%s)." % e.strerror
    if want == "soulsilver":
        if code == "IPGE":
            return True, "US SoulSilver - following Pokemon will be included."
        if code.startswith("IPK") or code.startswith("IPG"):
            return False, "That is HeartGold/SoulSilver from another region (%s). It needs US SoulSilver (IPGE)." % code
        return False, "That is not SoulSilver. Leave this box empty if you do not have it (it is optional)."
    if not re.match(r"^[A-Z0-9]{4}$", code):
        return False, "That file is not a DS game (.nds)."
    if code.startswith("CPU") and code != "CPUE":
        return False, "That is Platinum from another region (%s). You need the US version." % code
    if code in ("IPGE", "IPKE"):
        return False, "That is HeartGold/SoulSilver, not Platinum. Put it in the optional SoulSilver box."
    if code != "CPUE":
        return False, "That is not Pokemon Platinum (game code %s)." % code
    if rev == 0:
        return False, "That is US Platinum Rev 0. The build needs Rev 1 (the later printing)."
    if size < 134217728:
        return False, "That Platinum file is trimmed (%d MB). Use an untrimmed 128 MB dump." % (size // 1048576)
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != PLAT_SHA1:
        return False, "That Platinum file has been changed (patched or a bad dump). Use a clean dump of your cartridge."
    return True, "Correct game: Pokemon Platinum US Rev 1."


def find_sd_cards():
    """Folders that look like a 3DS SD card (have a 'Nintendo 3DS' folder)."""
    found = []
    if IS_WIN:
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if mask & (1 << i) and letter not in "AB":
                    root = letter + ":\\"
                    if ctypes.windll.kernel32.GetDriveTypeW(root) == 2 and os.path.isdir(root + "Nintendo 3DS"):
                        found.append(root)
        except Exception:
            pass
    else:
        import glob
        for root in glob.glob("/media/*/*") + glob.glob("/run/media/*/*") + glob.glob("/mnt/*") + glob.glob("/Volumes/*"):
            if os.path.isdir(os.path.join(root, "Nintendo 3DS")):
                found.append(root)
    return found


def sd_report(path):
    if not path:
        return None, "Optional. Without it, the game is built but not copied anywhere."
    if not os.path.isdir(path):
        return False, "Folder not found. Is the SD card plugged in?"
    if os.path.isdir(os.path.join(path, "Nintendo 3DS")):
        return True, "Looks like a 3DS SD card. The game goes in 3ds/platinum3ds (your save is backed up first)."
    for sub in ("3ds", "Nintendo 3DS"):
        if os.path.basename(os.path.normpath(path)).lower() == sub.lower():
            return False, "Pick the top of the SD card, not the '%s' folder inside it." % sub
    if os.path.isdir(os.path.join(path, "3ds")):
        return True, "Has a 3ds folder. The game goes in 3ds/platinum3ds."
    return False, "This does not look like a 3DS SD card (no 'Nintendo 3DS' folder). Pick the top of the card."


def find_project():
    """Best guess at the user's 3ds-platinum clone."""
    home = os.path.expanduser("~")
    cands = [os.path.join(HERE, ".."), os.path.join(HERE, "3ds-platinum"), os.path.join(HERE, "..", "3ds-platinum")]
    for base in (home, os.path.join(home, "Documents"), os.path.join(home, "Desktop"), os.path.join(home, "Downloads")):
        cands += [os.path.join(base, "3ds-platinum"), os.path.join(base, "3ds-platinum-main")]
    if not IS_WIN:
        cands.append("/workspace/platinum3ds")
    for c in cands:
        if os.path.isdir(os.path.join(c, "work_patches")) and os.path.isdir(os.path.join(c, "pspoke-patches")):
            return os.path.normpath(os.path.abspath(c))
    if IS_WIN:
        try:
            r = subprocess.run(["wsl.exe", "--", "bash", "-c",
                                'for d in ~/3ds-platinum ~/platinum3ds /workspace/platinum3ds; do '
                                '[ -d "$d/work_patches" ] && { readlink -f "$d"; break; }; done'],
                               capture_output=True, timeout=30, creationflags=NO_WINDOW)
            p = r.stdout.decode("utf-8", "replace").strip()
            if p.startswith("/"):
                return p
        except Exception:
            pass
    return ""


def project_report(path):
    if not path:
        return False, "Not found. Pick your 3ds-platinum folder."
    if path.startswith("/") and IS_WIN:
        return True, "Inside WSL (fast builds)."
    if os.path.isdir(os.path.join(path, "work_patches")) and os.path.isdir(os.path.join(path, "pspoke-patches")):
        slow = IS_WIN and wsl_unc_to_linux(path) is None
        return True, "Found." + (" It is on a Windows drive, so builds are slower than inside WSL." if slow else "")
    if os.path.isdir(os.path.join(path, "3ds-platinum", "work_patches")):
        return False, "Pick the 3ds-platinum folder inside this one."
    return False, "That folder is not the project (it has no work_patches folder)."


def explain(log_text, script_hint, failed_step):
    """One plain-English sentence for the result banner."""
    if script_hint:
        return script_hint
    for pat, msg in HINTS:
        if re.search(pat, log_text):
            return msg
    m = re.search(r"^[^\n]*\b(?:error|Error):[^\n]*$", log_text, re.M)
    if m:
        return "The game code did not compile. First error: " + m.group(0).strip()[:220]
    where = FRIENDLY.get(failed_step, failed_step or "the build")
    return "Something went wrong while %s. Press 'Copy details for help' and paste it to Claude." % where.lower()


def load_settings():
    try:
        with open(SETTINGS) as f:
            d = json.load(f)
    except Exception:
        d = {}
    out = {k: d.get(k, "") for k in FIELDS}
    out["legacy"] = bool(d.get("legacy"))
    return out


def save_settings(d):
    try:
        with open(SETTINGS, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


def run_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        print("This window needs Tk. On Ubuntu/WSL: sudo apt install python3-tk")
        sys.exit(1)

    root = tk.Tk()
    root.title("Platinum 3DS Builder")
    root.geometry("760x720")
    root.minsize(620, 560)
    cfg = load_settings()
    if not cfg["repo"]:
        cfg["repo"] = find_project()
    if not cfg["sd"]:
        cards = find_sd_cards()
        if len(cards) == 1:
            cfg["sd"] = cards[0]
    vars_ = {k: tk.StringVar(value=cfg.get(k, "")) for k in FIELDS}
    legacy = tk.BooleanVar(value=cfg["legacy"])
    status = tk.StringVar(value="")
    q = queue.Queue()
    st = {"proc": None, "busy": False, "cancel": False, "hint": "", "failed": "", "installed": "",
          "plat_ok": None, "planned": 0, "done_steps": 0}

    GOOD, BAD, MUTED = "#1a7f37", "#cf222e", "#57606a"
    big = ("Segoe UI", 11) if IS_WIN else ("TkDefaultFont", 11)
    head = ("Segoe UI", 16, "bold") if IS_WIN else ("TkDefaultFont", 16, "bold")
    style = ttk.Style()
    style.configure("Step.TLabel", font=(big[0], 11, "bold"))

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="Platinum 3DS Builder", font=head).pack(anchor="w")
    ttk.Label(outer, text="Pick your game and your SD card, then press the green button. Your files never leave this computer.",
              foreground=MUTED, wraplength=700).pack(anchor="w", pady=(0, 10))

    checks = {}

    def field(parent, key, title, kind, pick_label="Browse..."):
        box = ttk.Frame(parent)
        box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, text=title, style="Step.TLabel").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x")
        ent = ttk.Entry(row, textvariable=vars_[key], font=big)
        ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
        vars_[key].trace_add("write", lambda *_: ent.after(50, lambda: ent.xview("end")))   # show the file name end
        ent.after(300, lambda: ent.xview("end"))

        def pick():
            if kind == "dir":
                p = filedialog.askdirectory(title=title)
            else:
                p = filedialog.askopenfilename(title=title, filetypes=[("DS game", "*.nds"), ("All files", "*.*")])
            if p:
                vars_[key].set(os.path.normpath(p) if IS_WIN else p)
        ttk.Button(row, text=pick_label, command=pick).pack(side="left")
        msg = ttk.Label(box, text="", wraplength=700)
        msg.pack(anchor="w")
        checks[key] = msg
        return row

    field(outer, "platinum", "1.  Your Pokemon Platinum game file (.nds)", "file")
    sd_row = field(outer, "sd", "2.  Your 3DS SD card (plugged into this computer)", "dir")

    def find_sd():
        cards = find_sd_cards()
        if cards:
            vars_["sd"].set(cards[0])
            if len(cards) > 1:
                messagebox.showinfo("SD card", "Found more than one 3DS card: %s\nUsing %s." % (", ".join(cards), cards[0]))
        else:
            messagebox.showinfo("SD card", "No 3DS SD card found. Plug it in (it has a 'Nintendo 3DS' folder) and try again, "
                                           "or use Browse.")
    ttk.Button(sd_row, text="Find it", command=find_sd).pack(side="left", padx=(6, 0))
    field(outer, "soulsilver", "Optional:  SoulSilver game file (.nds) for following Pokemon", "file")

    go = tk.Button(outer, text="Build and install", font=(big[0], 15, "bold"), bg="#2da44e", fg="white",
                   activebackground="#2c974b", activeforeground="white", relief="flat", padx=20, pady=10,
                   cursor="hand2", command=lambda: start("auto"))
    go.pack(fill="x", pady=(4, 8))

    prog = ttk.Progressbar(outer, mode="determinate", maximum=100)
    prog.pack(fill="x")
    srow = ttk.Frame(outer)
    srow.pack(fill="x", pady=(4, 0))
    ttk.Label(srow, textvariable=status, font=big).pack(side="left")
    cancel_btn = ttk.Button(srow, text="Cancel", command=lambda: cancel(), state="disabled")
    cancel_btn.pack(side="right")

    banner = tk.Label(outer, text="", font=(big[0], 11, "bold"), wraplength=700, justify="left", anchor="w", padx=10, pady=8)
    brow = ttk.Frame(outer)

    def copy_details():
        text = log.get("1.0", "end")
        root.clipboard_clear()
        root.clipboard_append(text)
        messagebox.showinfo("Copied", "The details are copied. Paste them to Claude.\nThey are also saved in:\n" + LAST_LOG)

    def open_folder(p):
        try:
            if IS_WIN:
                if p.startswith("/"):    # a path inside WSL: ask WSL for its \\wsl.localhost\... name
                    r = subprocess.run(["wsl.exe", "--", "wslpath", "-w", p], capture_output=True, timeout=30,
                                       creationflags=NO_WINDOW)
                    p = r.stdout.decode("utf-8", "replace").strip() or p
                os.startfile(p)
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            messagebox.showinfo("Folder", "%s\n\n(%s)" % (p, e))
    ttk.Button(brow, text="Copy details for help", command=copy_details).pack(side="left")
    open_btn = ttk.Button(brow, text="Open game folder")

    # ---- details + advanced (folded away) ----------------------------------------------------------------
    folds = ttk.Frame(outer)
    folds.pack(fill="both", expand=True, pady=(8, 0))
    bar = ttk.Frame(folds)
    bar.pack(fill="x")
    details_btn = ttk.Button(bar, text="Show details")
    adv_btn = ttk.Button(bar, text="Advanced")
    details_btn.pack(side="left")
    adv_btn.pack(side="left", padx=(6, 0))

    adv = ttk.Frame(folds, padding=(0, 8, 0, 0))
    field(adv, "repo", "Project folder (your clone of 3ds-platinum)", "dir")
    ttk.Checkbutton(adv, text="Also apply the older patches/*.patch", variable=legacy).pack(anchor="w")
    abar = ttk.Frame(adv)
    abar.pack(fill="x", pady=(6, 0))
    adv_buttons = []
    for text, pipe in (("Check setup", "check"), ("Test my patches", "test"), ("Quick rebuild", "quick"),
                       ("Reinstall tools", "install"), ("Rebuild everything", "full")):
        b = ttk.Button(abar, text=text, command=lambda p=pipe: start(p))
        b.pack(side="left", padx=(0, 6))
        adv_buttons.append(b)

    logf = ttk.Frame(folds)
    log = tk.Text(logf, wrap="word", height=12, bg="#111", fg="#ddd", insertbackground="#ddd",
                  font=("Consolas" if IS_WIN else "monospace", 9))
    sb = ttk.Scrollbar(logf, command=log.yview)
    log.configure(yscrollcommand=sb.set, state="disabled")
    sb.pack(side="right", fill="y")
    log.pack(side="left", fill="both", expand=True)
    log.tag_configure("bad", foreground="#ff7b72")
    log.tag_configure("good", foreground="#7ee787")
    shown = {"details": False, "adv": False}

    def toggle(which, force=None):
        on = (not shown[which]) if force is None else force
        shown[which] = on
        w = logf if which == "details" else adv
        if on:
            w.pack(fill="both" if which == "details" else "x", expand=(which == "details"), after=bar)
        else:
            w.pack_forget()
        details_btn.configure(text="Hide details" if shown["details"] else "Show details")
        adv_btn.configure(text="Hide advanced" if shown["adv"] else "Advanced")
    details_btn.configure(command=lambda: toggle("details"))
    adv_btn.configure(command=lambda: toggle("adv"))

    def emit(text, tag=None):
        log.configure(state="normal")
        log.insert("end", text, tag)
        log.see("end")
        log.configure(state="disabled")

    def show_banner(ok, text, folder=""):
        banner.configure(text=text, bg="#dafbe1" if ok else "#ffebe9", fg=GOOD if ok else BAD)
        banner.pack(fill="x", pady=(8, 0), before=folds)
        brow.pack(fill="x", pady=(4, 0), before=folds)
        if folder:
            open_btn.configure(command=lambda: open_folder(folder))
            open_btn.pack(side="left", padx=(6, 0))
        else:
            open_btn.pack_forget()

    def hide_banner():
        banner.pack_forget()
        brow.pack_forget()

    # ---- live checks next to each box ------------------------------------------------------------------
    rom_cache = {}

    def set_check(key, ok, text):
        checks[key].configure(text=(("\u2714 " if ok else "\u2716 ") if ok is not None else "") + text,
                              foreground=GOOD if ok else (BAD if ok is False else MUTED))

    def check_rom(key, want):
        p = vars_[key].get().strip().strip('"')
        if not p:
            if key == "platinum":
                st["plat_ok"] = None
            set_check(key, None, "Required." if key == "platinum" else "Optional. Leave empty if you do not have it.")
            return
        if p in rom_cache:
            ok, text = rom_cache[p]
            set_check(key, ok, text)
            if key == "platinum":
                st["plat_ok"] = ok
            return
        set_check(key, None, "Checking the file...")

        def work():
            res = rom_report(p, want)
            rom_cache[p] = res
            q.put(("recheck", key, None))
        threading.Thread(target=work, daemon=True).start()

    def refresh(*_):
        check_rom("platinum", "platinum")
        check_rom("soulsilver", "soulsilver")
        set_check("sd", *sd_report(vars_["sd"].get().strip().strip('"')))
        set_check("repo", *project_report(vars_["repo"].get().strip().strip('"')))
    for k in FIELDS:
        vars_[k].trace_add("write", refresh)

    # ---- running -------------------------------------------------------------------------------------
    def current_cfg():
        return {k: vars_[k].get().strip().strip('"') for k in FIELDS}

    def set_busy(b):
        st["busy"] = b
        for x in adv_buttons:
            x.configure(state="disabled" if b else "normal")
        go.configure(state="disabled" if b else "normal", bg="#94d3a2" if b else "#2da44e")
        cancel_btn.configure(state="normal" if b else "disabled")

    def cancel():
        st["cancel"] = True
        status.set("Stopping...")
        try:
            subprocess.run(cancel_command(), capture_output=True, timeout=30, creationflags=NO_WINDOW)
        except Exception:
            pass
        p = st["proc"]
        if p and p.poll() is None:
            p.terminate()

    def run_steps(c, needs_root, steps):
        """Run one p3ds_build.sh call. Returns (ok, lines of ##NEED)."""
        argv = build_command(c, needs_root, steps, legacy.get())
        q.put(("log", "\n$ " + " ".join(steps) + (" (as admin)" if needs_root else "") + "\n", None))
        try:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        except OSError as e:
            q.put(("log", "Could not start: %s\n" % e, "bad"))
            return False, []
        st["proc"] = p
        needs = []
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").replace("\x00", "")
            if line.startswith("##STEP:"):
                _, name, what = line.strip().split(":", 2)
                if what == "START":
                    q.put(("step", name, None))
                elif what == "FAIL":
                    st["failed"] = name
                    q.put(("log", "FAILED: %s\n" % FRIENDLY.get(name, name), "bad"))
                elif what == "OK":
                    q.put(("stepdone", name, None))
                continue
            if line.startswith("##NEED:"):
                needs.append(line.strip()[7:])
                continue
            if line.startswith("##HINT:"):
                st["hint"] = line.strip()[7:]
                continue
            if line.startswith("##INSTALLED:"):
                st["installed"] = line.strip()[12:]
                continue
            if line.startswith("##DONE:"):
                continue
            tag = "bad" if line.startswith(("ERROR", "  [MISSING]", "  CONFLICT", "  FAIL")) else \
                  ("good" if line.startswith(("  [ok]", "  new", "  updated", "  removed")) else None)
            q.put(("log", line, tag))
        return p.wait() == 0 and not st["cancel"], needs

    def plan_auto(c, needs, has_ss):
        plan = []
        if "setup" in needs:
            plan.append((True, ["setup"]))      # setup also makes the link
        elif "link" in needs:
            plan.append((True, ["link"]))
        rest = ["stage"] + (["pspoke"] if "pspoke" in needs else []) + (["full"] if "full" in needs else [])
        rest += ["patches", "relink"] + (["followers"] if has_ss else []) + ["deploy"]
        plan.append((False, rest))
        return plan

    def worker(pipe, c):
        ok = True
        if pipe == "auto":
            st["planned"] = 1
            ok, needs = run_steps(c, False, ["needs"])
            plan = plan_auto(c, needs, bool(c.get("soulsilver"))) if ok else []
            if "full" in needs:
                q.put(("log", "First build: this one takes a long time. Later builds are quick.\n", None))
        else:
            plan = PIPELINES[pipe]
        st["planned"] = sum(len(s) for _, s in plan)
        st["done_steps"] = 0
        for needs_root, steps in plan:
            if not ok or st["cancel"]:
                ok = False
                break
            ok, _ = run_steps(c, needs_root, steps)
        q.put(("done", ok, pipe))

    def start(pipe):
        if st["busy"]:
            return
        c = current_cfg()
        repo_ok, repo_msg = project_report(c["repo"])
        if not repo_ok:
            toggle("adv", True)
            messagebox.showwarning("Project folder", "The builder cannot find your 3ds-platinum folder.\n\n" + repo_msg +
                                   "\n\nPick it in Advanced > Project folder.")
            return
        if pipe in ("auto", "full"):
            if not c["platinum"]:
                messagebox.showwarning("Game file", "Pick your Pokemon Platinum .nds file in box 1 first.")
                return
            ok, text = rom_cache.get(c["platinum"]) or rom_report(c["platinum"])
            if ok is False:
                messagebox.showwarning("Game file", text)
                return
            if c["soulsilver"]:
                ok, text = rom_cache.get(c["soulsilver"]) or rom_report(c["soulsilver"], "soulsilver")
                if ok is False:
                    messagebox.showwarning("SoulSilver file", text + "\n\nClear that box to build without following Pokemon.")
                    return
        if pipe in ("auto", "full", "quick") and c["sd"]:
            ok, text = sd_report(c["sd"])
            if ok is False and not messagebox.askyesno("SD card", text + "\n\nCopy the game there anyway?"):
                return
        if pipe in ("auto", "full", "quick") and not c["sd"]:
            if not messagebox.askyesno("No SD card", "No SD card is picked, so the game will be built but not copied "
                                                     "anywhere.\n\nContinue without the SD card?"):
                return
        w = wsl_state()
        if w != "ok":
            if w == "no-distro":
                messagebox.showerror("Linux needed", "WSL is installed but has no Linux yet.\n\nOpen PowerShell and run:\n"
                                                     "    wsl --install -d Ubuntu\nthen restart Windows and press the button again.")
                return
            if messagebox.askyesno("WSL needed", "This builder needs WSL (Linux inside Windows), which is not installed.\n\n"
                                                 "Install it now? Windows asks for permission, then you restart the computer "
                                                 "and press the button again."):
                try:
                    subprocess.run(["powershell", "-NoProfile", "-Command",
                                    "Start-Process wsl -ArgumentList '--install','-d','Ubuntu' -Verb RunAs"],
                                   creationflags=NO_WINDOW)
                    messagebox.showinfo("WSL", "When the installer finishes, restart Windows, open Ubuntu once to make a "
                                               "user name, then come back here.")
                except Exception as e:
                    messagebox.showerror("WSL", "Could not start the installer (%s).\nRun  wsl --install  in an admin PowerShell." % e)
            return
        if pipe == "full" and not messagebox.askokcancel("Rebuild everything", "This rebuilds the whole game from scratch and "
                                                         "takes a long time. Continue?"):
            return
        d = dict(c)
        d["legacy"] = legacy.get()
        save_settings(d)
        st.update(cancel=False, hint="", failed="", installed="")
        hide_banner()
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        prog.configure(value=0)
        set_busy(True)
        status.set("Starting...")
        threading.Thread(target=worker, args=(pipe, c), daemon=True).start()

    def finish(ok, pipe):
        set_busy(False)
        text = log.get("1.0", "end")
        try:
            with open(LAST_LOG, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
        c = current_cfg()
        if st["cancel"]:
            status.set("Cancelled.")
            show_banner(False, "Cancelled. Press the button again to carry on where it stopped.")
            return
        if not ok:
            status.set("Stopped.")
            show_banner(False, explain(text, st["hint"], st["failed"]))
            toggle("details", True)
            return
        prog.configure(value=100)
        status.set("Done.")
        if pipe in ("check", "test", "install"):
            msg = {"check": "Setup checked. Anything marked MISSING is installed by the green button.",
                   "test": "All your patches fit. Nothing was changed.",
                   "install": "Build tools installed."}[pipe]
            show_banner(True, msg)
            if pipe == "check":
                toggle("details", True)
        elif st["installed"]:
            show_banner(True, "Installed! Put the SD card back in your New 3DS, open the Homebrew Launcher and start "
                              "Platinum 3DS.", "")
        else:
            out = (c["repo"].rstrip("/\\") + ("/" if c["repo"].startswith("/") else os.sep) +
                   os.path.join("platinum3ds", "full"))
            show_banner(True, "Built! No SD card was picked, so the game is in the project's platinum3ds/full folder "
                              "(platinum3ds_full.3dsx).", out)

    def pump():
        try:
            while True:
                kind, a, b = q.get_nowait()
                if kind == "log":
                    emit(a, b)
                elif kind == "step":
                    n = max(st["planned"], 1)
                    status.set("Step %d of %d: %s..." % (min(st["done_steps"] + 1, n), n, FRIENDLY.get(a, a)))
                elif kind == "stepdone":
                    if a != "needs":
                        st["done_steps"] += 1
                    prog.configure(value=100.0 * st["done_steps"] / max(st["planned"], 1))
                elif kind == "recheck":
                    refresh()
                elif kind == "done":
                    finish(a, b)
        except queue.Empty:
            pass
        root.after(100, pump)

    def on_close():
        if st["busy"]:
            if not messagebox.askyesno("Still building", "A build is running. Stop it and close?"):
                return
            cancel()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)

    refresh()
    if not cfg["repo"]:
        toggle("adv", True)
    pump()
    root.mainloop()


if __name__ == "__main__":
    run_gui()
