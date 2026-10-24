#!/usr/bin/env python3
"""Apply this repo's work-tree patches to pspoke's generated tree, safely and repeatably.

work_patches/overlays/*.patch  flat names ("overlay005__fieldmap.c.patch" = overlays/source/overlay005__fieldmap.c);
                               include_*.patch are git-style header diffs for native-probe/pokeplatinum
work_patches/native/*.patch    git-style paths (a/native-audio-app/platform.c); applied with -p1 to
                               pspoke/port AND to the staged tree test_out (both must stay in sync)
patches/*.patch                older run-era diffs; paths in their headers are relative to the repo root (-p0)

How it stays safe while you keep editing patches:
- Every applied patch is remembered (a copy is kept next to the tree it was applied to). Next run:
  same patch -> skipped; edited patch -> the old version is taken out and the new one put in;
  deleted patch -> its old version is taken out; new patch -> applied.
- Everything is checked with dry runs first. If any patch would conflict, NOTHING is changed and the
  conflicts are listed (the tree is never left half-patched).
- Windows line endings (CRLF from a Windows git checkout) are converted before patching.
--check only reports what would happen. Keeps <out>/rebuild_pending.txt (overlay .c files to recompile;
the caller deletes it after a successful rebuild; '__ALL__' marks a header change).
"""
import argparse, os, shutil, subprocess, sys, tempfile

PATCH_FLAGS = ["--batch", "-s", "--no-backup-if-mismatch", "-r", "-"]


def norm(data):
    return data.replace(b"\r\n", b"\n")


def read(path):
    with open(path, "rb") as f:
        return norm(f.read())


def run_patch(args, data, cwd):
    return subprocess.run(["patch"] + PATCH_FLAGS + args, input=data, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def state(data, args, cwd):
    """'apply' (not in the tree yet) | 'done' (already in) | 'conflict'"""
    if run_patch(["-N", "--dry-run"] + args, data, cwd).returncode == 0:
        return "apply"
    if run_patch(["-R", "--dry-run"] + args, data, cwd).returncode == 0:
        return "done"
    return "conflict"


def touched(data, args):
    """Paths (relative to the patch's cwd) a patch touches."""
    if args and not args[0].startswith("-"):          # explicit target file (overlay patches)
        return [args[0]]
    strip = int(args[0][2:]) if args and args[0].startswith("-p") else 0
    out = []
    for line in data.splitlines():
        if line.startswith((b"--- ", b"+++ ")):
            p = line[4:].split(b"\t")[0].strip().decode(errors="replace")
            if p != "/dev/null":
                p = "/".join(p.split("/")[strip:])
                if p and p not in out:
                    out.append(p)
    return out


def replace_fits(old, cur, args, cwd):
    """Dry run of 'take old out, put cur in' on a scratch copy of the touched files."""
    with tempfile.TemporaryDirectory() as tmp:
        targs = list(args)
        for p in set(touched(old, args)) | set(touched(cur, args)):
            src = p if os.path.isabs(p) else os.path.join(cwd, p)
            dst = os.path.join(tmp, os.path.basename(p) if os.path.isabs(p) else p)
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
        if targs and not targs[0].startswith("-"):
            targs = [os.path.join(tmp, os.path.basename(targs[0]))]
        if run_patch(["-R"] + targs, old, tmp).returncode != 0:
            return False
        return state(cur, targs, tmp) == "apply"


class Item:
    """One patch file against one tree."""
    def __init__(self, label, cur, rec, args, cwd, kind):
        self.label, self.cur, self.rec, self.args, self.cwd, self.kind = label, cur, rec, args, cwd, kind
        self.old = read(rec) if os.path.exists(rec) else None

    def plan(self):
        """-> 'skip' | 'apply' | 'replace' | 'revert' | 'forget' | 'record' | 'conflict'"""
        cur, old = self.cur, self.old
        if cur is None:                              # patch file was deleted
            st = state(old, self.args, self.cwd)
            return {"done": "revert", "apply": "forget"}.get(st, "conflict")
        if old is not None and old != cur:           # patch file was edited since it was applied
            if state(old, self.args, self.cwd) == "done":
                return "replace" if replace_fits(old, cur, self.args, self.cwd) else "conflict"
        st = state(cur, self.args, self.cwd)
        if st == "done":
            return "skip" if old == cur else "record"
        return st

    def why(self):
        data = self.cur if self.cur is not None else self.old
        if self.cur is not None and self.old is not None and self.old != self.cur:
            return "        (you edited this patch, and the new version does not fit the source)"
        out = run_patch(["-N", "--dry-run"] + self.args, data, self.cwd).stdout.decode(errors="replace")
        return "\n".join("        " + l for l in out.strip().splitlines()[:6])


def remember(item):
    os.makedirs(os.path.dirname(item.rec), exist_ok=True)
    with open(item.rec, "wb") as f:
        f.write(item.cur)


def execute(item, action):
    """Run a planned action for real. Returns True if the tree changed."""
    if action in ("skip",):
        return False
    if action == "record":
        remember(item); return False
    if action == "forget":
        os.remove(item.rec); return False
    if action == "apply":
        r = run_patch(["-N"] + item.args, item.cur, item.cwd)
        if r.returncode != 0:
            raise RuntimeError(r.stdout.decode(errors="replace"))
        remember(item); return True
    if action == "revert":
        r = run_patch(["-R"] + item.args, item.old, item.cwd)
        if r.returncode != 0:
            raise RuntimeError(r.stdout.decode(errors="replace"))
        os.remove(item.rec); return True
    if action == "replace":
        r = run_patch(["-R"] + item.args, item.old, item.cwd)
        if r.returncode != 0:
            raise RuntimeError(r.stdout.decode(errors="replace"))
        if state(item.cur, item.args, item.cwd) != "apply":
            run_patch(["-N"] + item.args, item.old, item.cwd)   # put the old version back
            raise RuntimeError("the edited version does not fit the source (the old version was left in place)")
        r = run_patch(["-N"] + item.args, item.cur, item.cwd)
        if r.returncode != 0:
            run_patch(["-N"] + item.args, item.old, item.cwd)
            raise RuntimeError(r.stdout.decode(errors="replace"))
        remember(item); return True
    raise ValueError(action)


def collect(pdir, recdir, label_prefix, cwd, args_for, kind):
    """Items for every patch in pdir plus every remembered patch whose file was deleted."""
    names = set(f for f in os.listdir(pdir) if f.endswith(".patch")) if os.path.isdir(pdir) else set()
    if os.path.isdir(recdir):
        names |= set(f for f in os.listdir(recdir) if f.endswith(".patch"))
    items = []
    for f in sorted(names):
        src = os.path.join(pdir, f)
        cur = read(src) if os.path.isfile(src) else None
        items.append(Item(label_prefix + f[:-6], cur, os.path.join(recdir, f), args_for(f), cwd, kind))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="repo root (contains work_patches/, patches/, pspoke/)")
    ap.add_argument("--legacy", action="store_true", help="also try patches/*.patch")
    ap.add_argument("--check", action="store_true", help="only report what would happen; change nothing")
    ap.add_argument("--out", default=None, help="dir for rebuild_pending.txt (default: pspoke/.work)")
    ap.add_argument("--overlays", default=None, help="override overlays dir (tests)")
    ap.add_argument("--port", default=None, help="override pspoke/port dir (tests)")
    ap.add_argument("--stage", default=None, help="override staged test_out dir (tests)")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    port = a.port or os.path.join(root, "pspoke", "port")
    stage = a.stage or os.path.join(root, "pspoke", ".work", "tree", "test_out")
    ov = a.overlays or os.path.join(stage, "native-audio-app", "overlays")
    outdir = a.out or os.path.join(root, "pspoke", ".work")
    src_dir = os.path.join(ov, "source")
    pp_dir = os.path.join(stage, "native-probe", "pokeplatinum")

    for d, what in ((port, "pspoke/port"), (stage, "generated tree"), (src_dir, "overlay sources")):
        if not os.path.isdir(d):
            print(f"ERROR: {what} not found: {d}\n       (run the pspoke + full-build steps first)")
            return 2

    # Records live next to the tree they describe, so a regenerated tree starts with a clean slate.
    port_rec = os.path.join(port, ".p3ds_applied")
    stage_rec = os.path.join(stage, ".p3ds_applied")
    wp = os.path.join(root, "work_patches")

    items = []
    if a.legacy:
        items += collect(os.path.join(root, "patches"), os.path.join(stage_rec, "legacy"), "patches/", root,
                         lambda f: ["-p0"], "legacy")
    ndir = os.path.join(wp, "native")
    items += collect(ndir, os.path.join(port_rec, "native"), "port/", port, lambda f: ["-p1"], "native")
    items += collect(ndir, os.path.join(stage_rec, "native"), "stage/", stage, lambda f: ["-p1"], "native")
    odir = os.path.join(wp, "overlays")
    headers = [i for i in collect(odir, os.path.join(stage_rec, "headers"), "header ", pp_dir,
                                  lambda f: ["-p1"], "header") if i.label.startswith("header include_")]
    overlays = [i for i in collect(odir, os.path.join(stage_rec, "overlays"), "overlay ", src_dir,
                                   lambda f: [os.path.join(src_dir, f[:-6])], "overlay")
                if not i.label.startswith("overlay include_")]

    problems = []
    if headers and not os.path.isdir(pp_dir):
        problems += [(i.label, f"no {pp_dir}") for i in headers]
        headers = []
    for i in list(overlays):
        if not os.path.isfile(i.args[0]):
            overlays.remove(i)
            if i.cur is None:            # deleted patch for a file that is gone too: nothing to undo
                os.remove(i.rec); continue
            problems.append((i.label, "no file overlays/source/" + os.path.basename(i.args[0]) +
                             " (check the patch file name: folder/file.c is written folder__file.c)"))
    items += headers + overlays

    # ---- 1. plan everything with dry runs --------------------------------------------------------
    plans = []
    for i in items:
        p = i.plan()
        if p == "conflict":
            problems.append((i.label, "does not fit the current source:\n" + i.why()))
        else:
            plans.append((i, p))

    follower_src = os.path.join(odir, "p3ds_follower.h")
    follower_dst = os.path.join(src_dir, "p3ds_follower.h")
    follower_copy = os.path.isfile(follower_src) and (
        not os.path.exists(follower_dst) or read(follower_dst) != read(follower_src))

    if problems:
        print("\nThese patches cannot be installed, so NOTHING was changed:")
        for label, msg in problems:
            print(f"  CONFLICT  {label}: {msg}")
        print("\nFix or re-make those patches against the current source, then run this again.")
        return 1
    verbs = {"apply": "new", "replace": "updated", "revert": "removed"}
    counts = {"new": 0, "updated": 0, "removed": 0, "unchanged": 0}
    for i, p in plans:
        v = verbs.get(p, "unchanged")
        counts[v] += 1
        if v != "unchanged":
            print(f"  {v:<9} {i.label}")

    if a.check:
        print(f"\nCHECK OK: {counts['new']} new, {counts['updated']} updated, {counts['removed']} removed, "
              f"{counts['unchanged']} unchanged. Nothing was changed (check only).")
        return 0

    # ---- 2. do it --------------------------------------------------------------------------------
    changed_c, headers_changed, failed = [], False, []
    for i, p in plans:
        try:
            if execute(i, p):
                if i.kind == "header":
                    headers_changed = True
                elif i.kind == "overlay" and i.label.endswith(".c"):
                    changed_c.append(i.label[len("overlay "):].replace("__", "/"))
        except RuntimeError as e:
            print(f"  FAIL      {i.label}\n{e}")
            failed.append(i.label)
    if follower_copy:
        shutil.copyfile(follower_src, follower_dst)
        print("  copied    p3ds_follower.h")

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

    print(f"\nSUMMARY: {counts['new']} new, {counts['updated']} updated, {counts['removed']} removed, "
          f"{counts['unchanged']} unchanged, {len(failed)} failed")
    if failed:
        print("Failed (each one was left as it was before):")
        for x in failed:
            print("  - " + x)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
