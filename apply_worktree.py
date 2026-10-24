#!/usr/bin/env python3
"""Apply this repo's work-tree patches to pspoke's generated tree, idempotently.

work_patches/overlays/*.patch  flat names ("overlay005__fieldmap.c.patch" = overlays/source/overlay005__fieldmap.c);
                               include_*.patch are git-style header diffs for native-probe/pokeplatinum
work_patches/native/*.patch    git-style paths (a/native-audio-app/platform.c); applied with -p1 to
                               pspoke/port AND to the staged tree test_out (both must stay in sync)
patches/*.patch                older run-era diffs; paths in their headers are relative to the repo root (-p0)

Every patch is checked first: forward dry-run ok -> apply; reverse dry-run ok -> already applied, skip;
neither -> reported as a conflict (never half-applied). Prints a summary and keeps <out>/rebuild_pending.txt (overlay .c files to recompile; the caller deletes it
after a successful rebuild; '__ALL__' marks a header change).
"""
import argparse, os, shutil, subprocess, sys

def run_patch(args, patch, cwd):
    with open(patch, "rb") as f:
        return subprocess.run(["patch", "--batch", "-s"] + args, stdin=f, cwd=cwd,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def state(patch, target_args, cwd):
    """'apply' | 'done' | 'conflict'"""
    if run_patch(["-N", "--dry-run"] + target_args, patch, cwd).returncode == 0:
        return "apply"
    if run_patch(["-R", "--dry-run"] + target_args, patch, cwd).returncode == 0:
        return "done"
    return "conflict"

def apply_one(patch, target_args, cwd, label, stats):
    st = state(patch, target_args, cwd)
    if st == "apply":
        r = run_patch(["-N"] + target_args, patch, cwd)
        if r.returncode != 0:
            print(f"  FAIL      {label}\n{r.stdout.decode(errors='replace')}")
            stats["conflict"].append(label); return False
        print(f"  applied   {label}"); stats["applied"].append(label); return True
    if st == "done":
        print(f"  already   {label}"); stats["done"].append(label)
    else:
        print(f"  CONFLICT  {label}"); stats["conflict"].append(label)
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="repo root (contains work_patches/, patches/, pspoke/)")
    ap.add_argument("--legacy", action="store_true", help="also try patches/*.patch")
    ap.add_argument("--out", default=None, help="dir for changed_overlays.txt (default: pspoke/.work)")
    ap.add_argument("--overlays", default=None, help="override overlays dir (tests)")
    ap.add_argument("--port", default=None, help="override pspoke/port dir (tests)")
    ap.add_argument("--stage", default=None, help="override staged test_out dir (tests)")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    port = a.port or os.path.join(root, "pspoke", "port")
    stage = a.stage or os.path.join(root, "pspoke", ".work", "tree", "test_out")
    ov = a.overlays or os.path.join(stage, "native-audio-app", "overlays")
    outdir = a.out or os.path.join(root, "pspoke", ".work")
    stats = {"applied": [], "done": [], "conflict": [], "missing": []}
    changed_c = []

    for d, what in ((port, "pspoke/port"), (stage, "generated tree"), (ov, "overlays dir")):
        if not os.path.isdir(d):
            print(f"ERROR: {what} not found: {d}\n       (run the pspoke + full-build steps first)")
            return 2

    # ---- 1. legacy run-era diffs (header paths relative to repo root) -------------------------------
    if a.legacy:
        print("== legacy patches/*.patch ==")
        pdir = os.path.join(root, "patches")
        for f in sorted(os.listdir(pdir)):
            if f.endswith(".patch"):
                apply_one(os.path.join(pdir, f), ["-p0"], root, "patches/" + f, stats)

    # ---- 2. native patches: port/ and the staged tree -----------------------------------------------
    print("== work_patches/native (pspoke/port + staged tree) ==")
    ndir = os.path.join(root, "work_patches", "native")
    for f in sorted(os.listdir(ndir)):
        if not f.endswith(".patch"):
            continue
        p = os.path.join(ndir, f)
        apply_one(p, ["-p1"], port, "port/" + f[:-6], stats)
        apply_one(p, ["-p1"], stage, "stage/" + f[:-6], stats)

    # ---- 3. overlay patches ---------------------------------------------------------------------
    # Edited overlay sources are kept FLAT in overlays/source ("overlay005/fieldmap.c" -> overlay005__fieldmap.c),
    # which is exactly the patch name. include_*.patch are git-style diffs for the pokeplatinum tree the
    # overlays are compiled against (native-probe/pokeplatinum).
    print("== work_patches/overlays (generated overlay sources + headers) ==")
    src_dir = os.path.join(ov, "source")
    pp_dir = os.path.join(stage, "native-probe", "pokeplatinum")
    headers_changed = False
    odir = os.path.join(root, "work_patches", "overlays")
    for f in sorted(os.listdir(odir)):
        p = os.path.join(odir, f)
        if f == "p3ds_follower.h":
            dst = os.path.join(src_dir, f)
            if not os.path.exists(dst) or open(dst, "rb").read() != open(p, "rb").read():
                shutil.copyfile(p, dst); print("  copied    " + f); stats["applied"].append(f)
            else:
                print("  already   " + f); stats["done"].append(f)
            continue
        if not f.endswith(".patch"):
            continue
        name = f[:-6]
        if name.startswith("include_"):
            if not os.path.isdir(pp_dir):
                print(f"  MISSING   {f}  (no {pp_dir})"); stats["missing"].append(f); continue
            if apply_one(p, ["-p1"], pp_dir, "header " + name, stats):
                headers_changed = True
            continue
        target = os.path.join(src_dir, name)
        if not os.path.isfile(target):
            print(f"  MISSING   {f}  (no overlays/source/{name})"); stats["missing"].append(f); continue
        if apply_one(p, [target], src_dir, "overlay " + name, stats) and name.endswith(".c"):
            changed_c.append(name.replace("__", "/"))

    if headers_changed:
        print("  headers changed -> all overlay sources will be rebuilt")

    os.makedirs(outdir, exist_ok=True)
    # Pending list survives reruns until the caller deletes it after a successful rebuild.
    pend = os.path.join(outdir, "rebuild_pending.txt")
    old = set(open(pend).read().split()) if os.path.exists(pend) else set()
    if headers_changed:
        old.add("__ALL__")
    old.update(changed_c)
    if "__ALL__" in old:   # struct layout changed -> every overlay copy must be recompiled
        old = {n.replace("__", "/") for n in os.listdir(src_dir) if n.endswith(".c")} | {"__ALL__"}
    with open(pend, "w") as fh:
        fh.write("\n".join(sorted(old)) + ("\n" if old else ""))

    print(f"\nSUMMARY: {len(stats['applied'])} applied, {len(stats['done'])} already applied, "
          f"{len(stats['conflict'])} conflicts, {len(stats['missing'])} unmatched")
    if stats["conflict"] or stats["missing"]:
        print("Problems:")
        for x in stats["conflict"] + stats["missing"]:
            print("  - " + x)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
