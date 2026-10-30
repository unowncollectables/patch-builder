#!/usr/bin/env python3
"""Pokemon 3DS Builder - a one-button window around p3ds_build.sh (Platinum) and ss_build.sh (SoulSilver).

Tick Platinum and/or SoulSilver, pick the game file(s) you own and your SD card, press "Build my game and put it on the SD card". The window
downloads the project(s) if needed, works out which steps are still needed (tools, pspoke, full build), builds the
game(s) and copies them to the card (optionally also a HOME Menu .cia). Everything else lives under "Advanced".

On Windows it runs the build inside WSL2 (wsl.exe); on Linux it runs bash directly.
Package as an .exe with build_exe.bat (PyInstaller). Standard library only.
"""
import hashlib, json, os, re, string, subprocess, sys, threading, queue, time, urllib.request, webbrowser

VERSION = "1.2.1"   # bump before tagging a release; compared with the newest GitHub release
RELEASES_API = "https://api.github.com/repos/unowncollectables/patch-builder/releases/latest"
RELEASES_PAGE = "https://github.com/unowncollectables/patch-builder/releases/latest"

IS_WIN = os.name == "nt"
HERE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
# The .exe carries its own copy of the build scripts (build_exe.bat bundles them), so it works from any folder.
SCRIPTS = getattr(sys, "_MEIPASS", HERE)
SETTINGS = os.path.join(os.path.expanduser("~"), ".p3ds_builder.json")
LAST_LOG = os.path.join(os.path.expanduser("~"), "p3ds_builder_last_log.txt")
FIELDS = ["repo", "ssrepo", "platinum", "soulsilver", "out", "sd"]
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Documents", "Pokemon 3DS Builds")
PLAT_SHA1 = "0862ec35b24de5c7e2dcb88c9eea0873110d755c"
SS_SHA1 = "f8dc38ea20c17541a43b58c5e6d18c1732c7e582"   # pspoke's tested SoulSilver (US) dump; upstream.lock in the SoulSilver repo
GAMES = {   # per game: the build script, the config field holding its project folder, a file that marks that folder
    "platinum":   {"script": "p3ds_build.sh", "repo": "repo",   "marker": "work_patches",   "name": "Platinum",
                   "folders": ["3ds-platinum", "platinum3ds"]},
    "soulsilver": {"script": "ss_build.sh",   "repo": "ssrepo", "marker": "upstream.lock",  "name": "SoulSilver",
                   "folders": ["Pokemon-Soulsilver-New3ds", "pokemon-soulsilver-new3ds"]},
}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0
FRIENDLY = {"check": "Checking setup", "needs": "Working out what is needed", "setup": "Installing build tools (one time)",
            "link": "Linking project folder", "stage": "Checking your game file", "pspoke": "Preparing pspoke",
            "full": "Building the whole game (first time, long)", "patches": "Installing your patches",
            "testpatches": "Testing your patches", "relink": "Compiling and linking",
            "followers": "Making following-Pokemon data", "deploy": "Copying to the SD card",
            "get": "Downloading the project", "build": "Compiling the game (first time, long)", "cia": "Making the HOME Menu installer (.cia)"}

# Rough weight and expected seconds of each step: the progress bar fills by weight and creeps inside a long step,
# so a first-time install visibly moves instead of sitting at 0% for half an hour.
STEP_WEIGHT = {"setup": 6, "link": 1, "stage": 1, "pspoke": 4, "full": 20, "build": 20, "patches": 3, "testpatches": 3,
               "relink": 4, "followers": 2, "deploy": 1, "cia": 3, "check": 1}
STEP_SECS = {"setup": 600, "pspoke": 300, "full": 1500, "build": 1500, "relink": 240, "patches": 90, "cia": 180}
NEED_TEXT = {"setup": "the build tools (Linux packages and devkitPro)", "link": "the project folder link",
             "pspoke": "the Platinum game base (pspoke)", "full": "the first full build of the game"}

# Advanced buttons: name -> [(needs root, steps)]. "auto" (the big button) is planned at run time.
PIPELINES = {
    "check":   [(False, ["check"])],
    "install": [(True, ["setup", "link"])],
    "test":    [(False, ["testpatches"])],
    "quick":   [(False, ["patches", "relink", "deploy"])],
    "full":    [(False, ["check", "stage", "pspoke"]), (True, ["link"]),
                (False, ["full", "patches", "relink", "followers", "deploy"])],
    "cleantree": [(False, ["cleantree"])],
}
SS_PIPELINES = {   # the same Advanced buttons for SoulSilver
    "check":   [(False, ["check"])],
    "install": [(True, ["setup"])],
    "quick":   [(False, ["link", "deploy"])],
    "full":    [(False, ["check", "stage", "build", "link", "deploy"])],
    "cleantree": [(False, ["cleantree"])],
}

# Known failure text -> what to tell the user. First match wins; the script's own ##HINT lines win over these.
SD_STEPS = ("1. Put the SD card in your New 3DS and turn it on.\n"
            "2. Homebrew Launcher: start Platinum 3DS (file SD:/3ds/platinum3ds/platinum3ds_full.3dsx).\n"
            "3. Or for a HOME Menu icon: open FBI > SD > cias > platinum3ds.cia > Install.\n"
            "If you copy by hand: the .3dsx and your .nds (renamed platinum.nds) go in SD:/3ds/platinum3ds/, the .cia goes in SD:/cias/.")

HINTS = [
    (r"0x80370102|Virtual Machine Platform|virtualization|HCS_E_HYPERV_NOT_INSTALLED",
     "Windows cannot start Linux because virtualization is off. Turn on 'Virtualization' (Intel VT-x / AMD SVM) in your PC's BIOS/UEFI settings, then press the button again."),
    (r"Platinum project is not public|could not read Username|Repository not found|Authentication failed",
     "The Platinum project is not public yet, so the builder cannot download it. Nothing is wrong with your computer or game file. Wait for the public release, then press the button again."),
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


def drive_to_mnt(p):
    """'F:\\', 'F:', 'f:/', 'F:\\a\\b' -> '/mnt/f/', '/mnt/f/', '/mnt/f/', '/mnt/f/a/b' (else None)."""
    m = re.match(r"^([A-Za-z]):([\\/].*)?$", (p or "").strip().strip('"').strip())
    if not m:
        return None
    rest = (m.group(2) or "/").replace("\\", "/")
    return "/mnt/" + m.group(1).lower() + re.sub(r"/+", "/", rest)


def path_expr(p):
    """Shell expression giving the Linux path for a user-entered path (Windows paths via wslpath)."""
    if not p:
        return "''"
    if IS_WIN:
        lin = wsl_unc_to_linux(p)
        if lin is not None:
            return sq(lin)
        lin = drive_to_mnt(p)
        if lin is not None:      # X:\... -> /mnt/x/... directly; wslpath fails (prints nothing) for a drive WSL has not mounted
            return sq(lin)
        if len(p) > 1 and p[1] == ":" or p.startswith("\\\\"):
            return '"$(wslpath -a -u %s)"' % sq(p.replace("\\", "/") if len(p) > 1 and p[1] == ":" else p)
    return sq(p)


def build_command(cfg, root, steps, legacy=False, game="platinum"):
    """argv that runs `steps` of the game's build script; `root` runs it as root (WSL: -u root)."""
    exports = ["export P3DS_ROOT=" + path_expr(cfg[GAMES[game]["repo"]])]
    if cfg.get("cia"):
        exports.append("export P3DS_CIA=1")
    if cfg.get("sd") and not IS_WIN:      # on Windows the exe itself copies to the card (sd_copy), WSL never sees it
        exports.append("export P3DS_SD_WANTED=1")
    for key, var in (("platinum", "P3DS_PLATINUM"), ("soulsilver", "P3DS_SOULSILVER"), ("sd", "P3DS_SD"), ("out", "P3DS_OUT")):
        if key == "sd" and IS_WIN:
            continue
        if cfg.get(key):
            exports.append("export %s=%s" % (var, path_expr(cfg[key])))
    if legacy:
        exports.append("export P3DS_LEGACY=1")
    # setsid: the build gets its own process group, so Cancel can stop make/gcc too, not just bash.
    script = "; ".join(exports) + "; exec setsid -w bash " + path_expr(os.path.join(SCRIPTS, GAMES[game]["script"])) + " " + " ".join(steps)
    return wrap_script(script, root)


def get_command(cfg, game, what=None, root=False):
    """argv that downloads/updates a project folder (p3ds_get.sh); what='ensure-git' installs git as root."""
    exports = ["export P3DS_OFFLINE=1"] if cfg.get("offline") else []
    if cfg.get(GAMES[game]["repo"]):
        exports.append("export P3DS_DEST=" + path_expr(cfg[GAMES[game]["repo"]]))
    script = "; ".join(exports + ["exec bash " + path_expr(os.path.join(SCRIPTS, "p3ds_get.sh")) + " " + (what or game)])
    return wrap_script(script, root)


def wrap_script(script, root):
    if IS_WIN:
        return ["wsl.exe"] + (["-u", "root"] if root else []) + ["--", "bash", "-c", script]
    if not root or os.geteuid() == 0:
        return ["bash", "-c", script]
    return ["pkexec", "bash", "-c", script] if not sudo_ok() and which("pkexec") else ["sudo", "-n", "bash", "-c", script]


def sd_mount_command(sd):
    """Windows only: argv that mounts drive X: at /mnt/x inside WSL if WSL cannot see it yet (None if not a drive path)."""
    m = re.match(r"^([A-Za-z]):", sd or "")
    if not m:
        return None
    L = m.group(1).lower()
    script = ('d=/mnt/%s; mkdir -p "$d"; if [ -z "$(ls -A "$d" 2>/dev/null)" ]; then umount "$d" 2>/dev/null; '
              'mount -t drvfs %s: "$d" && echo "  [ok]      SD card %s: mounted at $d" '
              '|| echo "  (could not mount %s: in WSL)"; else echo "  [ok]      SD card %s: is visible at $d"; fi; true') % (L, L.upper(), L.upper(), L.upper(), L.upper())
    return ["wsl.exe", "-u", "root", "--", "bash", "-c", script]


def game_out_dir(repo, game):
    """Folder holding the built game. A Linux/WSL path is joined with '/', a Windows path with '\\'."""
    sub = ["platinum3ds" if game == "platinum" else "soulsilver3ds", "full"]
    if repo.startswith("/"):
        return "/".join([repo.rstrip("/")] + sub)
    return "\\".join([repo.rstrip("/\\")] + sub)


SD_LAYOUT = {   # game -> (sd folder, .3dsx name, .cia name on the card, nds name, save name)
    "platinum":   ("platinum3ds", "platinum3ds_full.3dsx", "platinum3ds.cia", "platinum.nds", "platinum.sav"),
    "soulsilver": ("soulsilver3ds", "soulsilver3ds.3dsx", "soulsilver3ds.cia", "soulsilver.nds", "soulsilver.sav"),
}


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sd_copy(game, out_dir, sd, rom, log=print):
    """Copy a finished game from the Windows output folder to the SD card, from Windows (no WSL involved).
    Backs up the save first, keeps one .prev of the old .3dsx, copies the user's .nds only if the card has none.
    Never deletes or overwrites a save. Returns (ok, message, sd game folder)."""
    import shutil, time
    folder, dotx, cia, nds, sav = SD_LAYOUT[game]
    name = GAMES[game]["name"]
    src = os.path.join(out_dir, name)
    if not os.path.isfile(os.path.join(src, dotx)):
        return False, "The finished %s game was not found in your output folder (%s). Press the button again." % (name, src), ""
    if not os.path.isdir(sd):
        return False, "The SD card (%s) is not there any more. Plug it in and press the button again." % sd, ""
    d = os.path.join(sd, "3ds", folder)
    try:
        os.makedirs(d, exist_ok=True)
        if os.path.isfile(os.path.join(d, sav)):
            os.makedirs(os.path.join(d, "save_backups"), exist_ok=True)
            bk = os.path.join(d, "save_backups", "%s_%s.sav" % (sav[:-4], time.strftime("%Y%m%d_%H%M%S")))
            shutil.copy2(os.path.join(d, sav), bk)
            log("  [ok]      save backed up to %s\n" % bk)
        if os.path.isfile(os.path.join(d, dotx)):
            shutil.copy2(os.path.join(d, dotx), os.path.join(d, dotx + ".prev"))
        shutil.copyfile(os.path.join(src, dotx), os.path.join(d, dotx))
        if _sha1(os.path.join(src, dotx)) != _sha1(os.path.join(d, dotx)):
            return False, "Copying to the SD card came out different. Re-seat the card and try again; it may be failing.", d
        log("  [ok]      %s copied to %s\n" % (dotx, d))
        if rom and os.path.isfile(rom) and not os.path.isfile(os.path.join(d, nds)):
            shutil.copyfile(rom, os.path.join(d, nds))
            log("  [ok]      your game file copied as %s\n" % nds)
        fb = os.path.join(src, "followers.bin")
        if game == "platinum" and os.path.isfile(fb):
            shutil.copyfile(fb, os.path.join(d, "followers.bin"))
        if os.path.isfile(os.path.join(src, cia)):
            os.makedirs(os.path.join(sd, "cias"), exist_ok=True)
            shutil.copyfile(os.path.join(src, cia), os.path.join(sd, "cias", cia))
            log("  [ok]      HOME Menu installer copied to %s (install it with FBI)\n" % os.path.join(sd, "cias", cia))
    except PermissionError:
        return False, "The SD card is write-protected or in use. Slide its lock switch up, plug it back in, and try again.", d
    except OSError as e:
        if getattr(e, "errno", 0) == 28:
            return False, "The SD card is full. Free up about 200 MB on it and press the button again.", d
        return False, "Copying to the SD card failed (%s). Re-seat the card and try again." % (e.strerror or e), d
    return True, "", d


def newer_release():
    """(tag, url) of a newer builder release on GitHub, or None (also None when offline or on any error)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(RELEASES_API, headers={"User-Agent": "p3ds-builder"}), timeout=8) as r:
            d = json.load(r)
        tag = d.get("tag_name", "")
        ver = lambda t: tuple(int(x) for x in re.findall(r"\d+", t))
        if tag and ver(tag) > ver(VERSION):
            return tag, d.get("html_url") or RELEASES_PAGE
    except Exception:
        pass
    return None


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


def install_wsl():
    """Windows: start 'wsl --install -d Ubuntu-24.04' elevated (UAC prompt). Nothing is left to run at boot.
    Ubuntu 24.04 because the animated 3D HOME Menu banner needs Python 3.12. Returns (ok, error text)."""
    try:
        # -Wait -PassThru: only report success if the elevated installer really ran and finished (a declined
        # permission box or a failed install gives a non-zero code); the installer gets its own visible console window.
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "try { $p = Start-Process wsl -ArgumentList '--install','-d','Ubuntu-24.04' -Verb RunAs "
                            "-WindowStyle Normal -Wait -PassThru; exit $p.ExitCode } catch { Write-Output $_.Exception.Message; exit 99 }"],
                           capture_output=True, creationflags=NO_WINDOW, timeout=3600)
        if r.returncode != 0:
            msg = (r.stdout + r.stderr).replace(b"\x00", b"").decode("utf-8", "replace").strip()
            return False, ("the permission box was declined or the install failed (code %d). %s" % (r.returncode, msg[:200])).strip()
    except Exception as e:
        return False, str(e)
    return True, ""


def clean_boot_entries():
    """Windows: remove anything this builder (any version) could have left to start at boot: Run/RunOnce values,
    scheduled tasks and Startup-folder shortcuts. Returns a list of what was removed (empty = nothing found)."""
    removed = []
    if not IS_WIN:
        return removed
    for hive in (r"HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"):
        for name in ("Pokemon3DSBuilder", "Pokemon 3DS Builder", "P3DSBuilder"):
            try:
                r = subprocess.run(["reg", "delete", hive, "/v", name, "/f"], capture_output=True, creationflags=NO_WINDOW, timeout=30)
                if r.returncode == 0:
                    removed.append("startup entry %s\\%s" % (hive.split("\\")[-1], name))
            except Exception:
                pass
    for name in ("Pokemon3DSBuilder", "Pokemon 3DS Builder", "P3DSBuilder"):
        try:
            r = subprocess.run(["schtasks", "/delete", "/tn", name, "/f"], capture_output=True, creationflags=NO_WINDOW, timeout=30)
            if r.returncode == 0:
                removed.append("scheduled task " + name)
        except Exception:
            pass
    try:
        folder = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        for n in os.listdir(folder):
            if "pokemon" in n.lower() and "builder" in n.lower():
                os.remove(os.path.join(folder, n))
                removed.append("Startup shortcut " + n)
    except Exception:
        pass
    return removed


def wsl_set_done():
    """WSL works: remember it so the installer is never offered again."""
    try:
        with open(SETTINGS + ".wsl", "w") as f:
            f.write("done")
    except Exception:
        pass


VIRT_RE = r"virtualization|virtual machine platform|0x80370102|0x80370114|hcs_e_|hyper-v|hypervisor"


def wsl_probe_text():
    """What 'wsl --status' says, cleaned up (shown to the user when WSL will not start)."""
    try:
        r = subprocess.run(["wsl.exe", "--status"], capture_output=True, timeout=60, creationflags=NO_WINDOW)
        return (r.stdout + r.stderr).replace(b"\x00", b"").decode("utf-8", "replace").strip()
    except Exception as e:
        return str(e)


def _wsl_state_file():
    """(attempts, time of the last attempt) from the small file beside the settings; attempts is 99 once WSL worked."""
    try:
        with open(SETTINGS + ".wsl") as f:
            t = f.read().strip()
        if t == "done":
            return 99, 0.0
        n, _, when = t.partition(",")
        return int(n or 0), float(when or 0)
    except Exception:
        return 0, 0.0


def wsl_tries():
    """How many times this builder already ran the WSL installer (99 = WSL has worked here)."""
    return _wsl_state_file()[0]


def wsl_tried():
    import time
    try:
        with open(SETTINGS + ".wsl", "w") as f:
            f.write("%d,%f" % (wsl_tries() + 1, time.time()))
    except Exception:
        pass


def restarted_since_install():
    """True if Windows has been restarted since the last WSL installer run (the first run only turns the features on;
    Ubuntu itself is downloaded by a second run after that restart)."""
    import time
    when = _wsl_state_file()[1]
    if not when:
        return True
    try:
        import ctypes
        up = ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:
        return True
    return time.time() - up > when + 5


def enable_wsl_requirements():
    """Windows: turn on the Windows features WSL2 needs and the hypervisor, elevated (UAC). Returns (ok, error text).
    Reversible: dism /disable-feature, bcdedit /set hypervisorlaunchtype off."""
    cmd = ("dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart; $a=$LASTEXITCODE; "
           "dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart; $b=$LASTEXITCODE; "
           "bcdedit.exe /set hypervisorlaunchtype auto; $c=$LASTEXITCODE; "
           "if (($a -ne 0 -and $a -ne 3010) -or ($b -ne 0 -and $b -ne 3010) -or $c -ne 0) { exit 1 } else { exit 0 }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "try { $p = Start-Process powershell -ArgumentList '-NoProfile','-Command',\"%s\" -Verb RunAs "
                            "-WindowStyle Normal -Wait -PassThru; exit $p.ExitCode } catch { Write-Output $_.Exception.Message; exit 99 }"
                            % cmd.replace('"', '`"')],
                           capture_output=True, creationflags=NO_WINDOW, timeout=1800)
    except Exception as e:
        return False, str(e)
    if r.returncode != 0:
        return False, "the permission box was declined or a step failed (code %d)" % r.returncode
    return True, ""


def wsl_state():
    """'ok' | 'no-wsl' | 'no-distro' | 'no-virt' (always 'ok' off Windows). 'no-virt' = Windows will not start WSL2
    (Virtual Machine Platform off, hypervisor off, or BIOS virtualization off); another restart will not fix that."""
    if not IS_WIN:
        return "ok"
    try:
        r = subprocess.run(["wsl.exe", "-e", "true"], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    except Exception:
        return "no-wsl"
    if r.returncode == 0:
        try:        # WSL runs, but is a real Ubuntu in it? (e.g. only Docker's hidden helper Linux counts as "working")
            lr = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=30, creationflags=NO_WINDOW)
            names = lr.stdout.replace(b"\x00", b"").decode("utf-8", "replace").lower().split()
            if names and not any(n.startswith(("ubuntu", "debian")) for n in names):
                return "no-distro"
        except Exception:
            pass
        return "ok"
    out = (r.stdout + r.stderr).replace(b"\x00", b"").decode("utf-8", "replace").lower()
    if re.search(VIRT_RE, out):
        return "no-virt"
    if "distribution" in out:
        return "no-distro"
    return "no-virt" if re.search(VIRT_RE, wsl_probe_text().lower()) else "no-wsl"


def preflight_problems(path, offline=False):
    """Plain-English warnings found before a build starts: low disk space, no internet. Empty list = all fine."""
    out = []
    try:
        import shutil
        d = path if os.path.isdir(path) else os.path.expanduser("~")
        free = shutil.disk_usage(d).free // (1 << 30)
        if free < 10:
            out.append("Only %d GB free on the drive for the finished game. The first build needs about 10 GB free." % free)
    except Exception:
        pass
    if not offline:
        try:
            urllib.request.urlopen("https://github.com", timeout=6).close()
        except Exception:
            out.append("This computer could not reach github.com. Check your internet connection (the builder downloads the "
                       "project and the build tools).")
    return out


def pc_report(path=""):
    """Plain-English health report of this PC for the Check setup button: [(ok, text)]."""
    out = []
    if IS_WIN:
        w = wsl_state()
        out.append((w == "ok", {"ok": "WSL + Ubuntu: installed and starting.",
                                "no-wsl": "WSL: not installed yet (the green button installs it).",
                                "no-distro": "WSL: installed but Ubuntu is missing (the green button installs it).",
                                "no-virt": "WSL: installed but Windows cannot start it (virtualization / Virtual Machine Platform / "
                                           "hypervisor is off). Press the green button for the one-click fix. Windows says: "
                                           + wsl_probe_text()[:200].replace("\n", " ")}[w]))
    probs = preflight_problems(path)
    out.append((not probs, "Disk space and internet: OK." if not probs else " ".join(probs)))
    return out


def find_nds(want):
    """Look for a matching .nds in the usual places (not a full disk scan). Returns a path or ''."""
    home = os.path.expanduser("~")
    dirs = [os.path.join(home, d) for d in ("Downloads", "Desktop", "Documents", "Games", "Roms", "ROMs")] + [home]
    if IS_WIN:
        dirs += [os.path.join(os.environ.get("OneDrive", ""), d) for d in ("Desktop", "Documents") if os.environ.get("OneDrive")]
    seen = set()
    for d in dirs:
        for depth, base in ((0, d), (1, d)):
            try:
                subs = [base] if depth == 0 else [os.path.join(d, x) for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))][:60]
            except OSError:
                continue
            for sub in subs:
                try:
                    names = os.listdir(sub)
                except OSError:
                    continue
                for n in names:
                    f = os.path.join(sub, n)
                    if n.lower().endswith(".nds") and f not in seen and os.path.isfile(f):
                        seen.add(f)
                        try:
                            ok, _ = rom_report(f, want)
                        except Exception:
                            ok = False
                        if ok:
                            return f
    return ""


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
        if code == "IPKE":
            return False, "That is HeartGold, not SoulSilver. This port is SoulSilver (US)."
        if code.startswith("CPU"):
            return False, "That is Pokemon Platinum, not SoulSilver. Put it in the Platinum box."
        if code != "IPGE":
            if code.startswith("IPG") or code.startswith("IPK"):
                return False, "That is HeartGold/SoulSilver from another region (%s). It needs US SoulSilver (IPGE)." % code
            return False, "That is not Pokemon SoulSilver."
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != SS_SHA1:
            return True, "US SoulSilver, but not the exact dump the port is tested with. It may still work; if the build fails, use a clean dump."
        return True, "Correct game: Pokemon SoulSilver US."
    if not re.match(r"^[A-Z0-9]{4}$", code):
        return False, "That file is not a DS game (.nds)."
    if code.startswith("CPU") and code != "CPUE":
        return False, "That is Platinum from another region (%s). You need the US version." % code
    if code in ("IPGE", "IPKE"):
        return False, "That is HeartGold/SoulSilver, not Platinum. Put it in the SoulSilver box."
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
        return None, "Optional. Without it, the game is still saved in the folder above."
    if not os.path.isdir(path):
        return False, "Folder not found. Is the SD card plugged in?"
    if os.path.isdir(os.path.join(path, "Nintendo 3DS")):
        return True, "Looks like a 3DS SD card. The games go in 3ds/platinum3ds and 3ds/soulsilver3ds (your saves are backed up first)."
    for sub in ("3ds", "Nintendo 3DS"):
        if os.path.basename(os.path.normpath(path)).lower() == sub.lower():
            return False, "Pick the top of the SD card, not the '%s' folder inside it." % sub
    if os.path.isdir(os.path.join(path, "3ds")):
        return True, "Has a 3ds folder. The games go in 3ds/platinum3ds and 3ds/soulsilver3ds."
    return False, "This does not look like a 3DS SD card (no 'Nintendo 3DS' folder). Pick the top of the card."


def find_project(game="platinum"):
    """Best guess at the user's clone of a port's project (empty = the builder downloads it)."""
    g = GAMES[game]
    home = os.path.expanduser("~")
    cands = [os.path.join(HERE, ".."), os.path.join(HERE, "..", "..")]
    for f in g["folders"]:
        cands += [os.path.join(HERE, f), os.path.join(HERE, "..", f)]
        for base in (home, os.path.join(home, "3ds"), os.path.join(home, "Documents"), os.path.join(home, "Desktop"), os.path.join(home, "Downloads")):
            cands += [os.path.join(base, f), os.path.join(base, f + "-main")]
    if game == "platinum" and not IS_WIN:
        cands.append("/workspace/platinum3ds")
    for c in cands:
        if is_project(c, game):
            return os.path.normpath(os.path.abspath(c))
    if IS_WIN:
        try:
            dirs = " ".join("~/" + f for f in g["folders"]) + (" /workspace/platinum3ds" if game == "platinum" else "")
            r = subprocess.run(["wsl.exe", "--", "bash", "-c",
                                'for d in %s; do [ -e "$d/%s" ] && { readlink -f "$d"; break; }; done' % (dirs, g["marker"])],
                               capture_output=True, timeout=30, creationflags=NO_WINDOW)
            p = r.stdout.decode("utf-8", "replace").strip()
            if p.startswith("/"):
                return p
        except Exception:
            pass
    return ""


def is_project(path, game):
    g = GAMES[game]
    if not os.path.exists(os.path.join(path, g["marker"])) or not os.path.isdir(os.path.join(path, "pspoke-patches")):
        return False
    return not (game == "platinum" and os.path.isfile(os.path.join(path, "upstream.lock")))   # that one is SoulSilver


def project_report(path, game="platinum"):
    g = GAMES[game]
    if not path:
        return None, "Not downloaded yet. The builder downloads it for you when you press the green button."
    if path.startswith("/") and IS_WIN:
        return True, "Inside WSL (fast builds)."
    if is_project(path, game):
        slow = IS_WIN and wsl_unc_to_linux(path) is None
        return True, "Found." + (" It is on a Windows drive, so builds are slower than inside WSL." if slow else "")
    for f in g["folders"]:
        if os.path.isdir(os.path.join(path, f)):
            return False, "Pick the %s folder inside this one." % f
    return False, "That folder is not the %s project (no %s inside). Clear the box and the builder downloads the project." % (g["name"], g["marker"])


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
    return "Something went wrong while %s. Press 'Copy details for help' and paste it into a GitHub issue (or to an AI helper)." % where.lower()


def load_settings():
    try:
        with open(SETTINGS) as f:
            d = json.load(f)
    except Exception:
        d = {}
    out = {k: d.get(k, "") for k in FIELDS}
    out["out"] = out["out"] or DEFAULT_OUT
    out["legacy"] = bool(d.get("legacy"))
    out["cia"] = bool(d.get("cia", True))
    out["offline"] = bool(d.get("offline"))   # beginners want both the .3dsx and the HOME Menu .cia
    out["want_plat"] = bool(d.get("want_plat", True))
    out["want_ss"] = bool(d.get("want_ss", False))
    out["autofind"] = bool(d.get("autofind", False))
    return out


def save_settings(d):
    try:
        with open(SETTINGS, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


def selected_games(c):
    return [g for g, k in (("platinum", "want_plat"), ("soulsilver", "want_ss")) if c.get(k)]


def plan_for(game, pipe, c, needs=None):
    """[(needs_root, steps)] for one game. pipe 'auto' uses the ##NEED lines from the game's 'needs' step."""
    cia = ["cia"] if c.get("cia") else []
    if pipe == "auto":
        needs = needs or []
        if game == "platinum":
            plan = []
            if "setup" in needs:
                plan.append((True, ["setup"]))      # setup also makes the link
            elif "link" in needs:
                plan.append((True, ["link"]))
            rest = ["stage"] + (["pspoke"] if "pspoke" in needs else []) + (["full"] if "full" in needs else [])
            rest += ["patches", "relink"] + (["followers"] if c.get("soulsilver") else []) + cia + ["deploy"]
            return plan + [(False, rest)]
        plan = [(True, ["setup"])] if "setup" in needs else []
        return plan + [(False, ["stage", "build", "link"] + cia + ["deploy"])]
    if game == "platinum":
        if pipe == "quick":
            return [(False, ["patches", "relink"] + cia + ["deploy"])]
        return PIPELINES[pipe]
    if pipe == "quick":
        return [(False, ["link"] + cia + ["deploy"])]
    return SS_PIPELINES.get(pipe, [])


def run_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        print("This window needs Tk. On Ubuntu/WSL: sudo apt install python3-tk")
        sys.exit(1)

    root = tk.Tk()
    root.title("Pokemon 3DS Builder v" + VERSION)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("%dx%d" % (min(800, sw - 40), min(860, sh - 80)))     # fits 1366x768 and 1080p
    root.minsize(600, 420)
    root.resizable(True, True)
    cfg = load_settings()
    for g in GAMES:
        if not cfg[GAMES[g]["repo"]]:
            cfg[GAMES[g]["repo"]] = find_project(g)
    if not cfg["sd"]:
        cards = find_sd_cards()
        if len(cards) == 1:
            cfg["sd"] = cards[0]
    vars_ = {k: tk.StringVar(value=cfg.get(k, "")) for k in FIELDS}
    legacy = tk.BooleanVar(value=cfg["legacy"])
    offline = tk.BooleanVar(value=cfg["offline"])
    want_plat = tk.BooleanVar(value=cfg["want_plat"])
    want_ss = tk.BooleanVar(value=cfg["want_ss"])
    want_cia = tk.BooleanVar(value=cfg["cia"])
    autofind = tk.BooleanVar(value=cfg["autofind"])
    status = tk.StringVar(value="")
    q = queue.Queue()
    st = {"proc": None, "busy": False, "cancel": False, "hint": "", "failed": "", "installed": [], "project": {},
          "planned": 0, "done_steps": 0, "cia": False,
          "plan_w": 1, "done_w": 0, "cur": "", "t0": 0, "t_step": 0, "last": ""}
    activity = tk.StringVar(value="")

    GOOD, BAD, MUTED = "#1a7f37", "#cf222e", "#57606a"
    big = ("Segoe UI", 11) if IS_WIN else ("TkDefaultFont", 11)
    head = ("Segoe UI", 16, "bold") if IS_WIN else ("TkDefaultFont", 16, "bold")
    style = ttk.Style()
    style.configure("Step.TLabel", font=(big[0], 11, "bold"))

    # The whole window scrolls (mouse wheel or scrollbar), so nothing is cut off on small screens.
    scroll_canvas = tk.Canvas(root, highlightthickness=0, borderwidth=0)
    vbar = ttk.Scrollbar(root, orient="vertical", command=scroll_canvas.yview)
    scroll_canvas.configure(yscrollcommand=vbar.set)
    vbar.pack(side="right", fill="y")
    scroll_canvas.pack(side="left", fill="both", expand=True)
    outer = ttk.Frame(scroll_canvas, padding=14)
    outer_id = scroll_canvas.create_window((0, 0), window=outer, anchor="nw")
    outer.bind("<Configure>", lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
    scroll_canvas.bind("<Configure>", lambda e: scroll_canvas.itemconfigure(outer_id, width=e.width))

    def on_wheel(e):
        if getattr(e, "num", 0) == 4:
            d = -1
        elif getattr(e, "num", 0) == 5:
            d = 1
        else:
            d = -1 if e.delta > 0 else 1
        w = root.winfo_containing(e.x_root, e.y_root)
        if w is not None and w.winfo_class() == "Text":     # the log scrolls itself
            return
        scroll_canvas.yview_scroll(d * 3, "units")
    root.bind_all("<MouseWheel>", on_wheel)
    root.bind_all("<Button-4>", on_wheel)
    root.bind_all("<Button-5>", on_wheel)
    ttk.Label(outer, text="Pokemon 3DS Builder", font=head).pack(anchor="w")
    ttk.Label(outer, text="Tick the game(s), pick the game file(s) you own and your SD card, then press the green button. "
                          "Your game files never leave this computer.",
              foreground=MUTED, wraplength=720).pack(anchor="w", pady=(0, 10))

    checks = {}
    boxes = {}

    def field(parent, key, title, kind, pick_label="Browse..."):
        box = ttk.Frame(parent)
        box.pack(fill="x", pady=(0, 10))
        boxes[key] = box
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
        msg = ttk.Label(box, text="", wraplength=720)
        msg.pack(anchor="w")
        checks[key] = msg
        return row

    ttk.Label(outer, text="1.  Which game(s) do you want on your 3DS?", style="Step.TLabel").pack(anchor="w")
    grow = ttk.Frame(outer)
    grow.pack(fill="x", pady=(2, 10))
    ttk.Checkbutton(grow, text="Pokemon Platinum", variable=want_plat).pack(side="left", padx=(0, 18))
    ttk.Checkbutton(grow, text="Pokemon SoulSilver", variable=want_ss).pack(side="left")
    ttk.Label(grow, text="  (both is fine)", foreground=MUTED).pack(side="left")
    ttk.Label(outer, text="Optional: following Pokemon for Platinum (your Pokemon walks behind you, like in HeartGold/SoulSilver). "
                          "It needs your SoulSilver game file too (box 2b). You do not have to build SoulSilver for this.",
              foreground=MUTED, wraplength=720).pack(anchor="w", pady=(0, 8))

    games_area = ttk.Frame(outer)
    games_area.pack(fill="x")
    field(games_area, "platinum", "2a.  Your Pokemon Platinum game file (.nds, US Rev 1)", "file")
    field(games_area, "soulsilver", "2b.  Your Pokemon SoulSilver game file (.nds, US). Optional for Platinum: adds following Pokemon", "file")
    def do_autofind(*_):
        if not autofind.get():
            return
        found = []
        for g in ("platinum", "soulsilver"):
            if not vars_[g].get().strip():
                f = find_nds(g)
                if f:
                    vars_[g].set(os.path.normpath(f) if IS_WIN else f)
                    found.append("%s: %s" % (GAMES[g]["name"], f))
        if found:
            messagebox.showinfo("Game file found", "Found and filled in:\n\n" + "\n".join(found) +
                                "\n\nIt is only read, never changed.")
    ttk.Checkbutton(games_area, text="Look for my game files automatically (Downloads, Desktop, Documents)",
                    variable=autofind, command=do_autofind).pack(anchor="w", pady=(0, 6))
    follow_note = ttk.Label(games_area, text="", foreground=MUTED, wraplength=720)
    field(outer, "out", "3.  Where to save the finished game on this PC (the .cia and .3dsx always go here)", "dir")
    sd_row = field(outer, "sd", "4.  Your 3DS SD card (optional, plugged into this computer)", "dir")

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
    ttk.Checkbutton(outer, text="Also make the installer for the HOME Menu (.cia, install it with FBI). Adds a few minutes.",
                    variable=want_cia).pack(anchor="w", pady=(0, 6))

    go = tk.Button(outer, text="Build my game and put it on the SD card", font=(big[0], 15, "bold"), bg="#2da44e", fg="white",
                   activebackground="#2c974b", activeforeground="white", relief="flat", padx=20, pady=10,
                   cursor="hand2", command=lambda: start("auto"))
    go.pack(fill="x", pady=(4, 2))
    ttk.Label(outer, text="Green: the normal button. Builds what you ticked and copies it to the SD card. "
              "Blue: the same, but always downloads the newest game code first.",
              foreground=MUTED, wraplength=720).pack(anchor="w", pady=(0, 6))
    upd = tk.Button(outer, text="Get the newest game updates from GitHub, then rebuild and copy",
                    font=(big[0], 12, "bold"), bg="#0969da", fg="white", activebackground="#0860ca", activeforeground="white",
                    relief="flat", padx=16, pady=6, cursor="hand2", command=lambda: start("update"))
    upd.pack(fill="x", pady=(0, 8))
    newer_lbl = tk.Label(outer, text="", fg="#0969da", cursor="hand2", font=(big[0], 11, "bold", "underline"))

    def check_newer():
        r = newer_release()
        if r:
            q.put(("newer", r[0], r[1]))
    threading.Thread(target=check_newer, daemon=True).start()

    prog = ttk.Progressbar(outer, mode="determinate", maximum=100)
    prog.pack(fill="x")
    ttk.Label(outer, textvariable=activity, foreground=MUTED, wraplength=720).pack(anchor="w", pady=(2, 0))
    srow = ttk.Frame(outer)
    srow.pack(fill="x", pady=(4, 0))
    ttk.Label(srow, textvariable=status, font=big).pack(side="left")
    cancel_btn = ttk.Button(srow, text="Cancel", command=lambda: cancel(), state="disabled")
    cancel_btn.pack(side="right")

    banner = tk.Label(outer, text="", font=(big[0], 11, "bold"), wraplength=720, justify="left", anchor="w", padx=10, pady=8)
    brow = ttk.Frame(outer)

    def copy_details():
        text = log.get("1.0", "end")
        root.clipboard_clear()
        root.clipboard_append(text)
        messagebox.showinfo("Copied", "The details are copied. Paste them into a GitHub issue for the game you were building (or to an AI helper).\nThey are also saved in:\n" + LAST_LOG)

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
    ttk.Label(adv, text="Project folders: leave empty and the builder downloads them for you (into your Linux/WSL home).",
              foreground=MUTED, wraplength=720).pack(anchor="w", pady=(0, 6))
    field(adv, "repo", "Platinum project folder (3ds-platinum)", "dir")
    field(adv, "ssrepo", "SoulSilver project folder (Pokemon-Soulsilver-New3ds)", "dir")
    ttk.Checkbutton(adv, text="Also apply the older Platinum patches/*.patch", variable=legacy).pack(anchor="w")

    def do_clean_boot():
        gone = clean_boot_entries()
        messagebox.showinfo("Boot entries", ("Removed:\n  " + "\n  ".join(gone)) if gone else
                            "Nothing to remove: this builder has nothing set to start at boot.")
    if IS_WIN:
        ttk.Button(adv, text="Clean up boot entries (stops it from starting at boot)", command=do_clean_boot).pack(anchor="w", pady=(6, 0))
    ttk.Checkbutton(adv, text="Offline: do not check GitHub for game updates on the green button (the blue button still does)",
                    variable=offline).pack(anchor="w")
    ttk.Label(adv, text="Maintenance tools. You normally never need these: the green button does everything. "
              "Each one is explained next to its button.", foreground=MUTED, wraplength=720).pack(anchor="w", pady=(10, 4))
    abar = ttk.Frame(adv)
    abar.pack(fill="x")
    abar.columnconfigure(1, weight=1)
    adv_buttons = []
    for i, (text, pipe, why) in enumerate((
            ("Check my setup", "check", "Only looks, changes nothing. Shows what is installed and what is missing. Use it first if something fails."),
            ("Test my patches (Platinum)", "test", "Checks that your Platinum patches still fit the game code. Nothing is built or changed."),
            ("Quick rebuild", "quick", "Re-applies patches and rebuilds only what changed, then copies it. Fast. Use after a small change."),
            ("Reinstall build tools", "install", "Downloads and installs the compilers again. Use if the check says a tool is broken or missing."),
            ("Rebuild everything (slow)", "full", "Rebuilds the whole game from scratch. Takes a long time. Use if a normal build gives odd results."),
            ("Delete build files (slow next time)", "cleantree", "Deletes the temporary build folder, so the next build starts from zero. Your saves, game files and SD card are kept."),
            ("Delete old builds", "clean", "Removes old finished builds to free disk space. You are asked before anything is deleted."))):
        b = ttk.Button(abar, text=text, command=lambda p=pipe: start(p))
        b.grid(row=i, column=0, sticky="ew", pady=2)
        ttk.Label(abar, text=why, foreground=MUTED, wraplength=470, justify="left").grid(row=i, column=1, sticky="w", padx=(10, 0))
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
        if ok and st.get("head"):
            text = text + "\nBuilt the latest version: " + "; ".join(
                "%s commit %s (%s): %s" % (GAMES.get(g, {}).get("name", g), h[0][:7], h[1], h[2]) for g, h in st["head"].items())
        if st.get("news"):
            text = "Game code on GitHub - " + "; ".join(st["news"]) + "\n" + text
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

    def check_rom(key):
        p = vars_[key].get().strip().strip('"')
        if not p:
            set_check(key, None, "Required for this game." if (want_plat if key == "platinum" else want_ss).get() else "")
            return
        if (p, key) in rom_cache:     # keyed by game too: the same file can be checked for the other game's box
            set_check(key, *rom_cache[(p, key)])
            return
        set_check(key, None, "Checking the file...")

        def work():
            rom_cache[(p, key)] = rom_report(p, key)
            q.put(("recheck", key, None))
        threading.Thread(target=work, daemon=True).start()

    def refresh(*_):
        # a game's boxes only show while its tick is on (the Platinum box also takes SoulSilver's ROM for following Pokemon)
        follow_note.pack_forget()
        for key, var in (("platinum", want_plat), ("soulsilver", want_ss)):
            # the SoulSilver box also shows for Platinum alone: that file is what adds following Pokemon
            if var.get() or (key == "soulsilver" and want_plat.get()):
                boxes[key].pack(fill="x", pady=(0, 10))
            else:
                boxes[key].pack_forget()
        if want_plat.get():
            if vars_["soulsilver"].get().strip():
                follow_note.configure(text="Platinum will get following Pokemon from your SoulSilver file.")
            else:
                follow_note.configure(text="Platinum will build without following Pokemon. That is fine. To add them, pick your "
                                           "SoulSilver file in 2b (optional).")
            follow_note.pack(anchor="w", pady=(0, 10))
        check_rom("platinum")
        check_rom("soulsilver")
        set_check("sd", *sd_report(vars_["sd"].get().strip().strip('"')))
        set_check("repo", *project_report(vars_["repo"].get().strip().strip('"'), "platinum"))
        set_check("ssrepo", *project_report(vars_["ssrepo"].get().strip().strip('"'), "soulsilver"))
    for k in FIELDS:
        vars_[k].trace_add("write", refresh)
    want_plat.trace_add("write", refresh)
    want_ss.trace_add("write", refresh)

    # ---- running -------------------------------------------------------------------------------------
    def current_cfg():
        c = {k: vars_[k].get().strip().strip('"') for k in FIELDS}
        c["want_plat"], c["want_ss"], c["cia"] = want_plat.get(), want_ss.get(), want_cia.get()
        c["offline"] = offline.get()
        c["autofind"] = autofind.get()
        c["out"] = c.get("out") or DEFAULT_OUT
        return c

    def set_busy(b):
        st["busy"] = b
        for x in adv_buttons:
            x.configure(state="disabled" if b else "normal")
        go.configure(state="disabled" if b else "normal", bg="#94d3a2" if b else "#2da44e")
        upd.configure(state="disabled" if b else "normal", bg="#8bb9f0" if b else "#0969da")
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

    def run_argv(argv, label, game=None):
        """Run one script call. Returns (ok, lines of ##NEED, exit code)."""
        q.put(("log", "\n$ " + label + "\n", None))
        try:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        except OSError as e:
            q.put(("log", "Could not start: %s\n" % e, "bad"))
            return False, [], 1
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
            if line.startswith("##CLEAN:"):
                st.setdefault("clean", []).append(line.strip()[8:])
                continue
            if line.startswith("##NEWS:"):
                _, g, news = line.strip().split(":", 2)
                st.setdefault("news", []).append("%s: %s" % (GAMES.get(g, {}).get("name", g), news))
                continue
            if line.startswith("##HEAD:"):
                _, g, rest = line.strip().split(":", 2)
                sha, date, title = (rest.split(" ", 2) + ["", ""])[:3]
                st.setdefault("head", {})[g] = (sha, date, title)
                continue
            if line.startswith("##REMOTE:"):
                _, g, sha = line.strip().split(":", 2)
                st.setdefault("remote", {})[g] = sha
                continue
            if line.startswith("##PROJECT:") and game:
                st["project"][game] = line.strip()[10:]
                q.put(("project", game, line.strip()[10:]))
                continue
            if line.startswith("##INSTALLED:"):
                st["installed"].append((game, line.strip()[12:]))
                continue
            if line.startswith("##DONE:"):
                continue
            tag = "bad" if line.startswith(("ERROR", "  [MISSING]", "  CONFLICT", "  FAIL")) else \
                  ("good" if line.startswith(("  [ok]", "  new", "  updated", "  removed")) else None)
            if line.strip():
                st["last"] = line.strip()
            q.put(("log", line, tag))
        rc = p.wait()
        return rc == 0 and not st["cancel"], needs, rc

    def run_steps(c, game, needs_root, steps):
        if False:   # v1.0.8: the SD copy is done from Windows (sd_copy), no WSL mount needed
            # A card plugged in after WSL started is often not visible under /mnt/<letter>; mount it (as admin) first.
            run_argv(sd_mount_command(c["sd"]), "make the SD card visible to the builder (as admin)", game)
        ok, needs, _ = run_argv(build_command(c, needs_root, steps, legacy.get(), game),
                                "%s: %s%s" % (GAMES[game]["name"], " ".join(steps), " (as admin)" if needs_root else ""), game)
        return ok, needs

    def fetch(c, game):
        """Download / update the game's project folder. Fills c[repo] with where it ended up."""
        ok, _, rc = run_argv(get_command(c, game), "download the %s project" % GAMES[game]["name"], game)
        if not ok and rc == 3 and not st["cancel"]:       # git itself is missing
            ok, _, _ = run_argv(get_command(c, game, "ensure-git", True), "install git (as admin)")
            if ok:
                ok, _, rc = run_argv(get_command(c, game), "download the %s project" % GAMES[game]["name"], game)
        if ok and game in st["project"]:
            c[GAMES[game]["repo"]] = st["project"][game]
        return ok

    def clean_worker(c):
        st["clean"] = []
        cc = dict(c)
        for g in selected_games(c):
            if c[GAMES[g]["repo"]]:
                run_argv(wrap_script("export P3DS_CLEAN_DRY=1; " + build_command(cc, False, ["clean"], False, g)[-1], False),
                         "list old files (%s)" % GAMES[g]["name"], g)
        items = list(st["clean"])
        ans = queue.Queue()
        q.put(("askclean", items, ans))
        if ans.get():
            for g in selected_games(c):
                if c[GAMES[g]["repo"]]:
                    run_argv(build_command(cc, False, ["clean"], False, g), "clean up (%s)" % GAMES[g]["name"], g)
        q.put(("done", True, "clean"))

    def worker(pipe, c):
        if pipe == "clean":
            return clean_worker(c)
        ok = build_once(pipe, c)
        # Right before copying: is the project still the newest on GitHub? If something was pushed during the build,
        # update and do a quick rebuild (at most 2 times), so the user always gets the latest version.
        tries = 0
        while ok and pipe == "auto" and not c.get("offline") and not st["cancel"] and tries < 2:
            st["remote"] = {}
            newer = []
            for g in selected_games(c):
                env = dict(c, offline=True)       # offline: only look, do not change the clone
                run_argv(wrap_script("export P3DS_CHECK_REMOTE=1; " + get_command(env, g)[-1], False),
                         "check GitHub for newer %s code" % GAMES[g]["name"], g)
                r, h = st["remote"].get(g), st.get("head", {}).get(g, ("",))[0]
                if r and h and r != h:
                    newer.append(g)
            if not newer:
                break
            tries += 1
            q.put(("log", "\nNew game code was pushed during the build - updating and rebuilding (%d of 2)...\n" % tries, None))
            ok = build_once(pipe, c)
        if ok:
            ok = copy_to_sd(pipe, c)
        q.put(("done", ok, pipe))

    def copy_to_sd(pipe, c):
        ok = True
        if IS_WIN and c.get("sd") and pipe in ("auto", "full", "quick") and not st["cancel"]:
            for g in selected_games(c):
                q.put(("log", "\n$ copy %s to the SD card (from Windows)\n" % GAMES[g]["name"], None))
                q.put(("step", "deploy", None))
                good, msg, d = sd_copy(g, c.get("out") or DEFAULT_OUT, c["sd"], c.get(g), lambda t: q.put(("log", t, None)))
                if not good:
                    st["hint"], st["failed"] = msg, "deploy"
                    q.put(("log", "ERROR: " + msg + "\n", "bad"))
                    return False
                st["installed"].append((g, d))
        return ok

    def build_once(pipe, c):
        ok = True
        if pipe == "check":
            q.put(("log", "This PC:\n", None))
            for good, text in pc_report(c["out"]):
                q.put(("log", "  [%s] %s\n" % ("ok" if good else "MISSING", text), "good" if good else "bad"))
        games = selected_games(c)
        plans = []      # (game, needs_root, steps)
        if pipe in ("auto", "full", "quick", "check", "install", "test", "cleantree"):
            if pipe == "test":
                games = [g for g in games if g == "platinum"]
            if pipe == "install":
                games = games[-1:]      # one tool install serves both (the SoulSilver one installs the superset)
            for g in games:
                if pipe == "auto" or not c[GAMES[g]["repo"]]:
                    if not fetch(c, g):
                        ok = False
                        break
            for g in games if ok else []:
                needs = []
                if pipe == "auto":
                    ok, needs = run_steps(c, g, False, ["needs"])
                    if not ok:
                        break
                    miss = [NEED_TEXT[n] for n in needs if n in NEED_TEXT]
                    q.put(("log", ("%s: still needed: %s.\n" % (GAMES[g]["name"], "; ".join(miss))) if miss else
                           "%s: everything it needs is already installed.\n" % GAMES[g]["name"], None))
                    if "full" in needs:
                        q.put(("log", "First build of %s: this one takes a long time. Later builds are quick.\n" % GAMES[g]["name"], None))
                for root_needed, steps in plan_for(g, pipe, c, needs):
                    plans.append((g, root_needed, steps))
        # the root steps (tool install) go first, and the tools are installed only once for both games
        ordered, seen_setup = [], False
        for g, rt, steps in [p for p in plans if p[1]]:
            if "setup" in steps:
                if seen_setup:
                    steps = ["link"] if g == "platinum" else []
                seen_setup = True
            if steps:
                ordered.append((g, rt, steps))
        ordered += [p for p in plans if not p[1]]
        st["planned"] = sum(len(s) for _, _, s in ordered)
        st["done_steps"] = 0
        st["plan_w"] = sum(STEP_WEIGHT.get(x, 2) for _, _, s in ordered for x in s)
        st["done_w"] = 0
        for g, rt, steps in ordered:
            if not ok or st["cancel"]:
                ok = False
                break
            ok, _ = run_steps(c, g, rt, steps)
        return ok

    def wsl_virt_help():
        """WSL is installed but Windows will not start it: explain and offer the fix (no more restart loops)."""
        said = wsl_probe_text()[:400]
        if messagebox.askyesno("WSL cannot start", "WSL is installed, but Windows cannot start it: virtualization is not "
                               "available to Windows. Restarting again will not fix this.\n\nWindows says:\n" + said +
                               "\n\nThis is common on PCs set up for VirtualBox or VMware. The builder can turn on the two Windows "
                               "settings WSL needs (Virtual Machine Platform, and Windows' own hypervisor). It is reversible. "
                               "Older VirtualBox/VMware may need an update afterwards.\n\nTurn them on now? "
                               "(Windows asks for permission; then restart once.)"):
            status.set("Turning on the Windows settings WSL needs. Please wait...")
            set_busy(True)
            st["t0"] = 0

            def fix_job():
                q.put(("wslfix",) + enable_wsl_requirements())
            threading.Thread(target=fix_job, daemon=True).start()
        else:
            messagebox.showinfo("WSL cannot start", "If it still fails after this, check that Virtualization "
                                "(Intel VT-x / AMD SVM) is on in the PC's BIOS/UEFI settings.")

    def start(pipe):
        if st["busy"]:
            return
        c = current_cfg()
        if pipe == "update":            # same as the big green button, but always fetches the latest from GitHub
            c["offline"] = False
            pipe = "auto"
        games = selected_games(c)
        if c.get("autofind"):
            for g in games:
                if not c[g]:
                    f = find_nds(g)
                    if f:
                        vars_[g].set(os.path.normpath(f) if IS_WIN else f)
                        c[g] = vars_[g].get()
        if not games:
            messagebox.showwarning("Which game?", "Tick Pokemon Platinum, Pokemon SoulSilver, or both, in step 1.")
            return
        for g in games:
            rok, rmsg = project_report(c[GAMES[g]["repo"]], g)
            if rok is False:
                toggle("adv", True)
                messagebox.showwarning("Project folder", "The %s project folder is not right.\n\n%s" % (GAMES[g]["name"], rmsg))
                return
        if pipe in ("auto", "full"):
            for g in games:
                if not c[g]:
                    messagebox.showwarning("Game file", "Pick your Pokemon %s .nds file first." % GAMES[g]["name"])
                    return
                ok, text = rom_cache.get((c[g], g)) or rom_report(c[g], g)
                if ok is False:
                    messagebox.showwarning("%s file" % GAMES[g]["name"], text)
                    return
            if c["soulsilver"] and "soulsilver" not in games:      # Platinum + a SoulSilver file = following Pokemon
                ok, text = rom_cache.get((c["soulsilver"], "soulsilver")) or rom_report(c["soulsilver"], "soulsilver")
                if ok is False:
                    messagebox.showwarning("SoulSilver file", text + "\n\nClear that box to build without following Pokemon.")
                    return
        if pipe in ("auto", "full", "quick") and c["sd"]:
            ok, text = sd_report(c["sd"])
            if ok is False and not messagebox.askyesno("SD card", text + "\n\nCopy the game there anyway?"):
                return
        if pipe in ("auto", "full", "quick") and not c["sd"]:
            if not messagebox.askyesno("No SD card", "No SD card is picked, so the game will only be saved in your output folder "
                                                     "(not copied to a card).\n\nContinue without the SD card?"):
                return
        if pipe in ("auto", "full"):
            warn = preflight_problems(c["out"], c.get("offline"))
            if warn and not messagebox.askyesno("Before we start", "\n\n".join(warn) + "\n\nContinue anyway?"):
                return
        w = wsl_state()
        if w == "no-virt":
            wsl_virt_help()
            return
        if w == "ok":
            wsl_set_done()
        tries = wsl_tries()
        if w != "ok" and tries >= 3:
            # Ran several times and WSL still is not ready: stop installing, show what Windows really says.
            messagebox.showwarning("WSL is not ready", "The WSL installer already ran on this PC %s times, so the builder "
                                   "will not run it again.\n\nWindows says:\n" % (tries if tries < 99 else "several") +
                                   (wsl_probe_text()[:300] or "(nothing)") +
                                   "\n\nTry, in this order:\n"
                                   "1. Restart the PC once.\n"
                                   "2. Open **Ubuntu 24.04** from the Start menu once. If it asks for a user name and password, "
                                   "type anything, then close it.\n"
                                   "3. Press the green button again.\n\n"
                                   "Still stuck? Advanced > Check setup shows a plain report. To run the installer yourself: "
                                   "PowerShell (admin) > wsl --install -d Ubuntu-24.04")
            return
        if w != "ok" and tries >= 1 and not restarted_since_install():
            messagebox.showinfo("Restart the computer", "The first part of the WSL setup is done, but Windows needs a restart "
                                "before the next part.\n\nRestart the PC, open this builder, and press the green button again. "
                                "(The builder will not ask to install again until you have restarted.)")
            return
        if w != "ok":
            what = ("Windows needs a small free add-on called WSL, which lets this builder compile the game. "
                    "Your Windows has it set up only partly." if w == "no-distro" else
                    "Windows needs a small free add-on called WSL, which lets this builder compile the game. "
                    "It is not installed yet.")
            if tries >= 1:
                what = ("The PC has restarted, but Ubuntu is not installed yet. The second part of the setup downloads it "
                        "(a few hundred MB).")
            if messagebox.askyesno("One-time setup (about 10 minutes)", what + "\n\nThis happens once, never again:\n"
                                   "  1. Click Yes, then Yes again when Windows asks for permission.\n"
                                   "  2. Wait for the install window to finish, then restart the computer.\n"
                                   "  3. Open this builder again yourself. If a black window asks for a user name and "
                                   "password, type anything you will remember.\n"
                                   "  4. Press the green button again. That is all. It is never asked again.\n\nInstall it now?"):
                status.set("Installing WSL + Ubuntu. Windows may show a black window for a few minutes. Please wait...")
                activity.set("Do not close this window. When the install finishes, a message here tells you to restart.")
                st["t0"] = 0
                set_busy(True)

                def wsl_job():
                    ok_, err_ = install_wsl()
                    if ok_:
                        wsl_tried()
                    q.put(("wsldone", ok_, (err_, wsl_state() if ok_ else "")))
                threading.Thread(target=wsl_job, daemon=True).start()
            return
        if pipe == "full" and not messagebox.askokcancel("Rebuild everything", "This rebuilds the whole game from scratch and "
                                                         "takes a long time. Continue?"):
            return
        if pipe == "cleantree" and not messagebox.askokcancel("Clean build tree", "This deletes the build tree (pspoke/.work) for the selected game(s), so the next build starts from scratch and takes a long time. Your saves, ROMs, downloaded sources and SD card are kept. Continue?"):
            return
        d = dict(c)
        d["legacy"] = legacy.get()
        d["offline"] = offline.get()
        save_settings(d)
        st.update(cancel=False, hint="", failed="", installed=[], project={}, cia=c["cia"], news=[], head={}, remote={})
        hide_banner()
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        prog.configure(value=0)
        st["t0"], st["last"], st["cur"] = time.time(), "", ""
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
        if pipe in ("check", "test", "install", "clean", "cleantree"):
            msg = {"clean": "Clean up finished.", "check": "Setup checked. Anything marked MISSING is installed by the green button.",
                   "test": "All your patches fit. Nothing was changed.",
                   "cleantree": "Build tree cleaned. The next build starts from scratch.",
                   "install": "Build tools installed."}[pipe]
            show_banner(True, msg)
            if pipe == "check":
                toggle("details", True)
        elif st["installed"]:
            names = " and ".join("%s 3DS" % GAMES[g]["name"] for g, _ in st["installed"] if g)
            more = " To use the HOME Menu version instead, install the .cia from the cias folder on the card with FBI." if st["cia"] else ""
            try:
                from tkinter import messagebox
                messagebox.showinfo("All done!", "Your game is on the SD card.\n\n" + SD_STEPS)
            except Exception:
                pass
            outd = c.get("out") or DEFAULT_OUT
            show_banner(True, "Installed! Put the SD card back in your New 3DS, open the Homebrew Launcher and start %s.%s A copy is also saved in %s." % (names, more, outd), outd)
        else:
            outd = c.get("out") or DEFAULT_OUT
            show_banner(True, "Built! No SD card was picked. Your game is saved in: " + outd +
                        "  (see 'How to put this on your 3DS.txt' there)", outd)
            try:
                from tkinter import messagebox
                messagebox.showinfo("Copy these files to your SD card", SD_STEPS + "\n\nYour finished game is saved in:\n" + outd)
            except Exception:
                pass

    def tick():
        """Once a second while busy: creep the bar inside the current step and show elapsed time + last output line."""
        if st["busy"] and st["t0"]:
            el = int(time.time() - st["t0"])
            cur = st["cur"]
            frac = 0.0
            if cur and cur not in ("needs", "get"):
                frac = min(0.9, (time.time() - st["t_step"]) / STEP_SECS.get(cur, 60)) * STEP_WEIGHT.get(cur, 2)
                prog.configure(value=100.0 * min(st["done_w"] + frac, st["plan_w"]) / max(st["plan_w"], 1))
            last = st["last"][:90]
            activity.set("Working for %d:%02d. Still going, safe to leave it.%s" %
                         (el // 60, el % 60, ("  Latest: " + last) if last else ""))
        root.after(1000, tick)

    def pump():
        try:
            while True:
                kind, a, b = q.get_nowait()
                if kind == "log":
                    emit(a, b)
                elif kind == "step":
                    st["cur"], st["t_step"] = a, time.time()
                    n = max(st["planned"], 1)
                    if a not in ("needs", "get"):
                        status.set("Step %d of %d: %s..." % (min(st["done_steps"] + 1, n), n, FRIENDLY.get(a, a)))
                    else:
                        status.set(FRIENDLY.get(a, a) + "...")
                elif kind == "stepdone":
                    if a not in ("needs", "get"):
                        st["done_steps"] += 1
                        st["done_w"] += STEP_WEIGHT.get(a, 2)
                    st["cur"] = ""
                    prog.configure(value=100.0 * min(st["done_w"], st["plan_w"]) / max(st["plan_w"], 1))
                elif kind == "wsldone":
                    set_busy(False)
                    activity.set("")
                    if a and b[1] == "ok":
                        status.set("WSL + Ubuntu are ready. Press the green button again.")
                        messagebox.showinfo("One-time setup", "WSL + Ubuntu are ready. Press the green button again.")
                    elif a and b[1] == "no-virt":
                        status.set("")
                        wsl_virt_help()
                    elif a:
                        status.set("Restart the computer now.")
                        messagebox.showinfo("One-time setup", "The installer finished. Now restart the computer.\n\n"
                                            "Then open this builder yourself and press the green button. Nothing starts by itself at boot.")
                    else:
                        status.set("")
                        messagebox.showerror("One-time setup", "The installer did not finish: %s\n\nYou can do it by hand: open "
                                             "PowerShell as administrator (right-click Start), run:\n    wsl --install -d Ubuntu-24.04\n"
                                             "then restart the computer and open this builder again." % b[0])
                elif kind == "wslfix":
                    set_busy(False)
                    if a:
                        status.set("Restart the computer, then open this builder again.")
                        messagebox.showinfo("WSL settings", "Done. Restart the computer now, then open this builder and press "
                                            "the green button. If WSL still will not start after that, turn on Virtualization "
                                            "(Intel VT-x / AMD SVM) in the PC's BIOS/UEFI settings.")
                    else:
                        status.set("")
                        messagebox.showerror("WSL settings", "Could not turn them on: %s\n\nBy hand, in PowerShell as administrator:\n"
                                             "  dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart\n"
                                             "  bcdedit /set hypervisorlaunchtype auto\nthen restart." % b)
                elif kind == "askclean":
                    if not a:
                        messagebox.showinfo("Clean up", "Nothing to clean up. Your saves, save backups, game files and the build cache are always kept.")
                        b.put(False)
                    else:
                        shown_items = "\n".join(a[:25]) + ("\n... and %d more" % (len(a) - 25) if len(a) > 25 else "")
                        b.put(messagebox.askyesno("Clean up old builds", "These old files will be removed:\n\n" + shown_items +
                                                  "\n\nSaves, save backups, your game files, settings and the build cache are never removed. Remove them?"))
                elif kind == "newer":
                    newer_lbl.configure(text="A newer builder is available (%s, you have v%s). Click here to download it." % (a, VERSION))
                    newer_lbl.bind("<Button-1>", lambda e, u=b: webbrowser.open(u))
                    newer_lbl.pack(anchor="w", after=upd, pady=(0, 6))
                elif kind == "project":
                    vars_[GAMES[a]["repo"]].set(b)
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

    if IS_WIN:      # older versions registered a resume-at-boot entry: remove any leftovers, silently
        threading.Thread(target=clean_boot_entries, daemon=True).start()
    refresh()
    pump()
    tick()
    root.mainloop()


if __name__ == "__main__":
    run_gui()
