#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Credited fan-tier follower sprites for Black 2 / White 2 (build time only).

Converts the user-supplied PokeCommunity overworld sprite pack (the .rar /
.zip the user picks in the builder, or an already-extracted folder) into
`followers_fan.bin`, the strip pack the port's follower code loads from the
SD game folder at runtime. Runs on the build machine only; nothing from the
pack is ever committed to any repository -- the pack is an input the user
supplies, exactly like their ROM.

WHAT GETS CONVERTED -- THE LICENSE ALLOWLIST IS THE LAW HERE.
Only sheets drawn by the two artists whose terms were verified free-to-use
with credit on Nov 27, 2026:

  * csandoval / CSc-A7X / myumorph, "Unova Overworlds" (the base set):
    "Use them freely, we made it for all of you, just give proper credit!!!"
  * 2and2makes5, the shiny set, whose page defers to those same terms.

The allowlist below is an explicit table of (species, form) pairs -- the
103 TPoke-missing species of followers-design.md SS2.4 plus the form sheets
those same two artists cover (Deerling / Sawsbuck seasonals, Meloetta
Pirouette, Genesect drives). The converter opens ONLY the files named by
that table, so everything else in the pack is excluded BY CONSTRUCTION,
never by filtering after the fact:

  * every Generation I-IV sheet (those are rips from Nintendo's games --
    never usable in this project),
  * every species outside the 103,
  * 646_1 / 646_2 (Zyon17's Black / White Kyurem) and 646s_1 / 646s_2
    (MissingLukey's shiny versions of them): Zyon17's recovered terms
    require asking permission before using or recreating his work, and
    his account is gone, so that permission cannot be obtained. Those
    two forms stay on the port's battle-sprite floor.

If any allowlisted sheet is missing from the supplied pack the converter
FAILS LOUDLY (exit 3) and writes nothing -- a partial fan tier would be a
silent quality lie.

OUTPUT FORMAT (followers_fan.bin, all integers little-endian):
  header   "PBFN", u16 version (1), u16 entry count
  dir      per entry: u16 species, u8 form, u8 reserved (0),
           u32 offset of the normal strip record, u32 offset of the
           shiny strip record (offsets are absolute, from file start)
  record   u8 frameSize (32 or 64), then a floor-format strip
           (followers floor pipeline, PIPELINE.md): 8 frames of
           frameSize x frameSize 4bpp palette indices in controller-14
           order [up, down, right, left] x [frame 1, frame 2],
           nibble-packed over the wide strip image exactly as the
           runtime strip buffer holds them, then the strip's
           16-colour RGB555 palette (u16 x 16, entry 0 unused).
           The size rides in the record because one entry's two sheets
           can disagree: Reuniclus's shiny sheet is drawn at half the
           scale of its normal sheet (256px vs 512px), so a shiny
           Reuniclus follows at 32px where a normal one follows at 64.

CONVERSION RULES (the floor pipeline's geometry, PIPELINE.md SS3):
each source sheet is 4 direction rows [down, left, right, up] x 4 frame
columns; columns 0 and 1 are the two distinct walk poses (columns 2 and 3
repeat them -- verified by pairwise diff). The 8 used cells share one
union content box and one scale (min(frame/box_w, frame/box_h), capped at
2.0x), resampled nearest-neighbour on palette indices, anchored
bottom-centre with feet on the frame floor. No mirroring anywhere: the
pack has true left and right art, unlike the floor.

Two honest deviations from the floor's own conversion, both forced by the
fan art itself and recorded here rather than hidden:
  * COLOURS: battle sprites are already 16-colour; these sheets are not
    (up to 33 distinct colours). Each strip's palette is the 15 most-used
    opaque colours of its used cells, measured in RGB555 space; any
    other colour is mapped to its nearest palette colour. The per-entry
    report counts the remapped pixels.
  * SHINY: the floor shares one index strip between its normal and
    shiny palettes because battle data is a pure palette swap. The
    pack's shiny sheets are NOT pure palette swaps for 29 of the 62
    base entries (the shiny artist redrew details; Reuniclus's shiny
    sheet is laid out differently entirely). Each entry therefore
    carries TWO strips, one per shininess, each converted from its own
    sheet; the game picks the strip matching the lead. Same strip
    format, twice -- not a new format.

Sex is not keyed: the pack carries one gender's art for the three
gender-variant species (Unfezant, Frillish, Jellicent), so the fan tier
serves that art for either sex. Their female floor strips remain the
fallback only when no pack is supplied.

Standard library only (the builder's rule). RAR extraction needs an
external tool -- unar/lsar, unrar, or 7z -- and is SELECTIVE: the archive
is listed first and only allowlisted members are extracted. ZIP packs are
read selectively with zipfile. A folder input is read in place.
"""

import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib

# --------------------------------------------------------------------------
# THE ALLOWLIST. Species: the 103 TPoke-missing species (followers-design.md
# SS2.4). Forms: only the extra forms whose sheets are by the two verified
# artists (see the module docstring). Form 0 (the base sheet) is always
# included for every species. Anything not named here is never opened.
# --------------------------------------------------------------------------
SPECIES_103 = (
    494, 495, 496, 497, 498, 499, 500, 501, 502, 503, 504, 506, 508, 509,
    512, 514, 516, 517, 518, 520, 521, 522, 524, 526, 528, 530, 532, 534,
    536, 537, 540, 541, 543, 544, 547, 549, 551, 552, 554, 555, 557, 560,
    562, 563, 565, 566, 567, 568, 570, 571, 573, 574, 576, 577, 579, 581,
    582, 584, 585, 586, 589, 590, 591, 592, 593, 595, 597, 598, 599, 601,
    602, 603, 604, 606, 608, 609, 611, 612, 614, 615, 617, 620, 623, 625,
    627, 629, 633, 634, 635, 636, 637, 638, 639, 640, 641, 642, 643, 644,
    645, 646, 647, 648, 649,
)
EXTRA_FORMS = {
    585: (1, 2, 3),   # Deerling seasons (csandoval)
    586: (1, 2, 3),   # Sawsbuck seasons (csandoval)
    648: (1,),        # Meloetta Pirouette (csandoval)
    649: (1, 2, 3, 4),  # Genesect drives (csandoval)
    # 646 forms 1 and 2 are NOT here: Zyon17's Black/White Kyurem sheets
    # are permission-required (see docstring) and stay excluded.
}

ENTRIES = tuple(
    (species, form)
    for species in SPECIES_103
    for form in (0,) + EXTRA_FORMS.get(species, ())
)


def sheet_name(species, form, shiny):
    stem = "%03d%s" % (species, "s" if shiny else "")
    if form:
        stem += "_%d" % form
    return stem + ".png"


ALLOWLIST_FILES = tuple(
    sheet_name(species, form, shiny)
    for (species, form) in ENTRIES
    for shiny in (False, True)
)

CREDITS_TEXT = """\
Follower sprite credits -- credited fan tier (Black 2 / White 2 port)
=====================================================================

This build includes overworld follower sprites for the 103 Pokemon that
have no walking sprite in Black 2 / White 2 itself. They were converted
at build time, on this computer, from the fan-made overworld sprite pack
the user supplied to the builder. No sprite from the pack is distributed
with the port or its builder; the pack is an input, like the user's own
game ROM, and this file is generated only when that input is provided.

The sprites used are from "Unova Overworlds" and its shiny companion
set, used under the artists' stated terms: free to use with proper
credit.

  Unova Overworlds (base sprites) ... CSc-A7X / csandoval / myumorph
      "Use them freely, we made it for all of you, just give proper
      credit!!!" -- https://www.deviantart.com/myumorph
  Unova Overworlds, shiny versions .. 2and2makes5
      (released under the same terms as the base set)

Pack compilation .................... MissingLukey
Additional credited contributors .... Pokegirl4ever, Fernandojl,
    Silver-Skies, TyranitarDark, Getsuei-H, Kid1513, Milomilotic11,
    Kyt666, kdiamo11, Chocosrawlooid, Syledude, Gallanty,
    Gizamimi-Pichu, Kymoyonian

NOT USED, on purpose:
  * The pack's Generation I-IV sheets are rips from Nintendo's games.
    This port never uses ripped game assets; those species use sprites
    from the user's own game.
  * Black Kyurem and White Kyurem overworld sprites by Zyon17, and
    MissingLukey's shiny versions of them, were NOT used. Zyon17's
    terms require asking permission before using or recreating his
    work ("Ask permission before using or recreating my work in
    anyway. Plz give credit if used."), and his account is gone, so
    that permission cannot be obtained. Those two forms use the port's
    own battle-sprite conversion instead.

Converted by fan_sprites.py (part of the Pokemon 3DS patch-builder),
which converts only the sheets named in its license allowlist.
"""


class Fail(Exception):
    """A user-facing failure: message printed plainly, exit code attached."""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Minimal PNG reader (stdlib). Supports 8-bit non-interlaced colour types
# 0 (gray), 2 (RGB), 3 (palette), 6 (RGBA) -- everything the pack uses.
# Returns (width, height, bytearray of RGBA quads, row-major).
# --------------------------------------------------------------------------
def read_png(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise Fail("%s is not a PNG file" % os.path.basename(path))
    pos = 8
    idat = bytearray()
    palette = None
    trns = None
    header = None
    while pos + 8 <= len(data):
        (length,) = struct.unpack_from(">I", data, pos)
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif ctype == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body) - 2, 3)]
        elif ctype == b"tRNS":
            trns = body
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if header is None:
        raise Fail("%s has no PNG header" % os.path.basename(path))
    width, height, depth, colour_type, _comp, _filt, interlace = header
    if depth != 8 or interlace != 0 or colour_type not in (0, 2, 3, 6):
        raise Fail("%s uses an unsupported PNG encoding (depth %d, type %d, "
                   "interlace %d)" % (os.path.basename(path), depth, colour_type, interlace))
    channels = {0: 1, 2: 3, 3: 1, 6: 4}[colour_type]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    cursor = 0
    for _y in range(height):
        filt = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        if filt == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif filt != 0:
            raise Fail("%s uses an unknown PNG filter" % os.path.basename(path))
        out += line
        prev = line
    rgba = bytearray(width * height * 4)
    if colour_type == 6:
        rgba[:] = out
    elif colour_type == 2:
        for i in range(width * height):
            rgba[4 * i:4 * i + 3] = out[3 * i:3 * i + 3]
            rgba[4 * i + 3] = 255
    elif colour_type == 0:
        for i in range(width * height):
            rgba[4 * i] = rgba[4 * i + 1] = rgba[4 * i + 2] = out[i]
            rgba[4 * i + 3] = 255
    else:  # palette
        if palette is None:
            raise Fail("%s is a palette PNG with no palette" % os.path.basename(path))
        for i in range(width * height):
            index = out[i]
            if index >= len(palette):
                raise Fail("%s references a missing palette colour" % os.path.basename(path))
            rgba[4 * i:4 * i + 3] = bytes(palette[index])
            rgba[4 * i + 3] = trns[index] if trns and index < len(trns) else 255
    return width, height, rgba


# --------------------------------------------------------------------------
# Sheet -> floor-format strip.
# --------------------------------------------------------------------------
def rgb555(r, g, b):
    return (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)


def convert_sheet(path):
    """One pack sheet -> (frame_size, indices_bytes, palette_u16s, stats)."""
    width, height, px = read_png(path)
    if width != height or width % 4:
        raise Fail("%s is not a square 4x4 overworld sheet" % os.path.basename(path))
    cell = width // 4
    if cell == 64:
        frame = 32
    elif cell == 128:
        frame = 64
    else:
        raise Fail("%s has an unexpected cell size (%d px)" % (os.path.basename(path), cell))

    def pixel(cx, cy, x, y):
        at = ((cy * cell + y) * width + (cx * cell + x)) * 4
        return px[at], px[at + 1], px[at + 2], px[at + 3]

    # Palette: histogram of opaque colours (RGB555 space) over the 8 used
    # cells (rows 0-3, columns 0-1); keep the 15 most used.
    hist = {}
    for cy in range(4):
        for cx in (0, 1):
            for y in range(cell):
                for x in range(cell):
                    r, g, b, a = pixel(cx, cy, x, y)
                    if a >= 128:
                        v = rgb555(r, g, b)
                        hist[v] = hist.get(v, 0) + 1
    ranked = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))
    kept = [v for v, _count in ranked[:15]]
    kept_set = set(kept)
    palette_of = {}
    remapped = 0
    for v in hist:
        if v in kept_set:
            palette_of[v] = kept.index(v) + 1
        else:
            vr, vg, vb = v & 31, (v >> 5) & 31, (v >> 10) & 31
            best, best_dist = None, None
            for i, kv in enumerate(kept):
                kr, kg, kb = kv & 31, (kv >> 5) & 31, (kv >> 10) & 31
                dist = (vr - kr) ** 2 + (vg - kg) ** 2 + (vb - kb) ** 2
                if best_dist is None or dist < best_dist:
                    best, best_dist = i + 1, dist
            palette_of[v] = best
            remapped += hist[v]

    # Index grids for the 8 used cells, and their union content box in
    # cell coordinates (one box for the whole strip: no walk jitter).
    grids = {}
    box = [cell, cell, 0, 0]
    for cy in range(4):
        for cx in (0, 1):
            grid = bytearray(cell * cell)
            for y in range(cell):
                for x in range(cell):
                    r, g, b, a = pixel(cx, cy, x, y)
                    if a >= 128:
                        grid[y * cell + x] = palette_of[rgb555(r, g, b)]
                        if x < box[0]:
                            box[0] = x
                        if y < box[1]:
                            box[1] = y
                        if x + 1 > box[2]:
                            box[2] = x + 1
                        if y + 1 > box[3]:
                            box[3] = y + 1
            grids[(cy, cx)] = grid
    if box[2] == 0:
        raise Fail("%s appears to be empty" % os.path.basename(path))
    bw, bh = box[2] - box[0], box[3] - box[1]
    scale = min(frame / bw, frame / bh)
    if scale > 2.0:
        scale = 2.0
    nw = max(1, int(bw * scale + 0.5))
    nh = max(1, int(bh * scale + 0.5))
    ox, oy = (frame - nw) // 2, frame - nh

    # Strip order (controller 14): up, down, right, left pairs; the
    # sheet rows are [down, left, right, up]; columns 0/1 are the pair.
    row_for = (3, 0, 2, 1)  # up <- row 3, down <- row 0, right <- row 2, left <- row 1
    wide = bytearray(8 * frame * frame)  # index image, 8 frames side by side
    for slot, cy in enumerate(row_for):
        for step, cx in enumerate((0, 1)):
            grid = grids[(cy, cx)]
            base_x = (2 * slot + step) * frame
            for dy in range(nh):
                sy = box[1] + dy * bh // nh
                for dx in range(nw):
                    sx = box[0] + dx * bw // nw
                    wide[(oy + dy) * 8 * frame + base_x + ox + dx] = grid[sy * cell + sx]
    packed = bytearray(len(wide) // 2)
    for i, v in enumerate(wide):
        if i & 1:
            packed[i // 2] |= v << 4
        else:
            packed[i // 2] |= v
    palette = [0] * 16
    for i, v in enumerate(kept):
        palette[i + 1] = v
    stats = {"colours": len(hist), "remapped_pixels": remapped}
    return frame, bytes(packed), palette, stats


# --------------------------------------------------------------------------
# Pack input: a folder, a .zip, or a .rar. Only allowlisted sheets are
# ever read. Archives are listed first and extracted selectively where
# the available tool allows it.
# --------------------------------------------------------------------------
def find_sheet_root(base):
    """The folder directly holding the sheets, wherever it sits under base.

    Extractors and hand-made re-zips wrap the sheet folder in extra levels
    (an archive-named folder around the pack's own folder), so search the
    whole extracted tree instead of only the base and one level down."""
    for root, dirs, _files in os.walk(base):
        dirs.sort()
        if os.path.isfile(os.path.join(root, ALLOWLIST_FILES[0])):
            return root
    return None


def _run(argv, **kw):
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)


def _which(*names):
    for name in names:
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if os.path.isfile(os.path.join(directory, name)):
                return os.path.join(directory, name)
    return None


def obtain_sheets(pack, workdir):
    """Materialise the pack's sheet folder under workdir; return its path."""
    if os.path.isdir(pack):
        root = find_sheet_root(pack)
        if root is None:
            raise Fail("no sprite sheets found in that folder (looked for %s)" % ALLOWLIST_FILES[0])
        return root
    lower = pack.lower()
    if lower.endswith(".zip"):
        import zipfile
        try:
            archive = zipfile.ZipFile(pack)
        except zipfile.BadZipFile:
            raise Fail("that .zip file could not be read")
        wanted = set(ALLOWLIST_FILES)
        found = set()
        for info in archive.infolist():
            base = os.path.basename(info.filename)
            if base in wanted and not info.is_dir():
                archive.extract(info, workdir)
                found.add(base)
        missing = wanted - found
        if missing:
            raise Fail("the pack is missing %d allowlisted sheet(s): %s"
                       % (len(missing), ", ".join(sorted(missing)[:8])), code=3)
        root = find_sheet_root(workdir)
        if root is None:
            raise Fail("the extracted pack has an unexpected layout")
        return root
    if lower.endswith(".rar"):
        return _obtain_from_rar(pack, workdir)
    raise Fail("the follower sprite pack must be a .rar, a .zip, or an extracted folder")


def _obtain_from_rar(pack, workdir):
    wanted = set(ALLOWLIST_FILES)
    lsar = _which("lsar")
    unar = _which("unar")
    unrar = _which("unrar")
    seven = _which("7z", "7za", "7zz")
    members = None
    if lsar:
        out = _run([lsar, pack])
        if out.returncode == 0:
            members = [line.strip() for line in out.stdout.decode("utf-8", "replace").splitlines()
                       if line.strip().lower().endswith(".png")]
    elif unrar:
        out = _run([unrar, "lb", pack])
        if out.returncode == 0:
            members = [line.strip() for line in out.stdout.decode("utf-8", "replace").splitlines()
                       if line.strip().lower().endswith(".png")]
    elif seven:
        out = _run([seven, "l", "-slt", pack])
        if out.returncode == 0:
            members = [line.split("=", 1)[1].strip()
                       for line in out.stdout.decode("utf-8", "replace").splitlines()
                       if line.startswith("Path =") and line.strip().lower().endswith(".png")]
    chosen = None
    if members is not None:
        chosen = [m for m in members if os.path.basename(m) in wanted]
        have = {os.path.basename(m) for m in chosen}
        missing = wanted - have
        if missing:
            raise Fail("the pack is missing %d allowlisted sheet(s): %s"
                       % (len(missing), ", ".join(sorted(missing)[:8])), code=3)
    dest = os.path.join(workdir, "rar")
    os.makedirs(dest, exist_ok=True)
    if chosen is not None and unar:
        result = _run([unar, "-q", "-o", dest, pack] + chosen)
    elif chosen is not None and unrar:
        result = _run([unrar, "x", "-q", "-o+", pack] + chosen + [dest + os.sep])
    elif chosen is not None and seven:
        result = _run([seven, "x", "-y", "-o" + dest, pack] + chosen)
    elif unar:
        print("note: could not list the archive, so it was unpacked in full "
              "into a temp folder; only allowlisted sheets were read.")
        result = _run([unar, "-q", "-o", dest, pack])
    elif unrar:
        print("note: could not list the archive, so it was unpacked in full "
              "into a temp folder; only allowlisted sheets were read.")
        result = _run([unrar, "x", "-q", "-o+", pack, dest + os.sep])
    elif seven:
        print("note: could not list the archive, so it was unpacked in full "
              "into a temp folder; only allowlisted sheets were read.")
        result = _run([seven, "x", "-y", "-o" + dest, pack])
    else:
        raise Fail("no RAR reader is installed (need unar, unrar, or 7z). "
                   "Run the builder's setup step, or supply the pack as a .zip "
                   "or an extracted folder instead.")
    if result.returncode != 0:
        raise Fail("extracting the sprite pack failed: %s"
                   % result.stderr.decode("utf-8", "replace").strip()[:200])
    root = find_sheet_root(dest)
    if root is None:
        raise Fail("the extracted pack has an unexpected layout")
    return root


# --------------------------------------------------------------------------
# Assembly.
# --------------------------------------------------------------------------
def build(pack, out_dir, report_path=None):
    with tempfile.TemporaryDirectory(prefix="fan-sprites-") as workdir:
        root = obtain_sheets(pack, workdir)
        missing = [name for name in ALLOWLIST_FILES
                   if not os.path.isfile(os.path.join(root, name))]
        if missing:
            raise Fail("the pack is missing %d allowlisted sheet(s): %s"
                       % (len(missing), ", ".join(missing[:8])), code=3)
        records = []  # (species, form, normal_record, shiny_record, stats)
        for species, form in ENTRIES:
            pair = []
            entry_stats = {}
            for shiny in (False, True):
                name = sheet_name(species, form, shiny)
                frame, indices, palette, stats = convert_sheet(os.path.join(root, name))
                pair.append(struct.pack("<B", frame) + indices
                            + struct.pack("<16H", *palette))
                entry_stats["shiny" if shiny else "normal"] = dict(stats, frame_size=frame)
            records.append((species, form, pair[0], pair[1], entry_stats))
    header_size = 8 + 12 * len(records)
    blob = bytearray()
    directory = bytearray()
    offset = header_size
    for species, form, normal, shiny, _stats in records:
        directory += struct.pack("<HBBII", species, form, 0, offset, offset + len(normal))
        blob += normal
        blob += shiny
        offset += len(normal) + len(shiny)
    out_bin = os.path.join(out_dir, "followers_fan.bin")
    out_credits = os.path.join(out_dir, "FOLLOWER_CREDITS.txt")
    tmp_bin, tmp_credits = out_bin + ".tmp", out_credits + ".tmp"
    with open(tmp_bin, "wb") as f:
        f.write(struct.pack("<4sHH", b"PBFN", 1, len(records)))
        f.write(directory)
        f.write(blob)
    with open(tmp_credits, "w", newline="\n") as f:
        f.write(CREDITS_TEXT)
    os.replace(tmp_bin, out_bin)
    os.replace(tmp_credits, out_credits)
    total_remapped = sum(s["normal"]["remapped_pixels"] + s["shiny"]["remapped_pixels"]
                         for _sp, _f, _n, _s, s in records)
    print("Follower fan tier: converted %d entries (%d species) from the "
          "user-supplied sprite pack." % (len(records), len(SPECIES_103)))
    print("Sprite credits: Unova Overworlds by csandoval / CSc-A7X / myumorph; "
          "shiny set by 2and2makes5; pack compiled by MissingLukey.")
    print("Full credits are in FOLLOWER_CREDITS.txt beside followers_fan.bin.")
    print("Not used: the pack's Gen I-IV game rips, and Zyon17's Black/White "
          "Kyurem sheets (permission-required; the author cannot be reached).")
    if total_remapped:
        print("Colour note: %d pixels across all sheets were mapped to the "
              "nearest of each strip's 15 palette colours (the fan sheets "
              "use more colours than the game's 16-colour strips hold)."
              % total_remapped)
    if report_path:
        report = {
            "entries": [
                {"species": species, "form": form,
                 "normal": stats["normal"], "shiny": stats["shiny"]}
                for species, form, _n, _s, stats in records
            ],
            "total_remapped_pixels": total_remapped,
            "bin_bytes": header_size + len(blob),
        }
        with open(report_path, "w") as f:
            json.dump(report, f, indent=1)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert the user-supplied fan overworld sprite pack into "
                    "followers_fan.bin + FOLLOWER_CREDITS.txt (allowlist-only).")
    parser.add_argument("--pack", required=True,
                        help="the pack: a .rar, a .zip, or an extracted folder")
    parser.add_argument("--out", required=True,
                        help="output folder (the staged game folder)")
    parser.add_argument("--report", help="optional JSON conversion report path")
    args = parser.parse_args(argv)
    if not os.path.exists(args.pack):
        print("fan_sprites: pack not found: %s" % args.pack, file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)
    try:
        return build(args.pack, args.out, args.report)
    except Fail as e:
        print("fan_sprites: %s" % e, file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
