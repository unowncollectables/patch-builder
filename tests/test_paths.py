"""python3 -m unittest discover tests   (no GUI, no WSL needed)"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import p3ds_builder as b


class DrivePaths(unittest.TestCase):
    def test_drive_forms(self):
        for p in ("F:\\", "F:", "f:/", "F:/", " F:\\ ", '"F:\\"'):
            self.assertEqual(b.drive_to_mnt(p), "/mnt/f/", p)
        self.assertEqual(b.drive_to_mnt("E:\\SD card\\x"), "/mnt/e/SD card/x")
        self.assertIsNone(b.drive_to_mnt("/media/me/SD"))
        self.assertIsNone(b.drive_to_mnt("\\\\wsl$\\Ubuntu\\home"))

    def test_sd_not_given_to_wsl_on_windows(self):
        old = b.IS_WIN
        b.IS_WIN = True
        try:
            for p in ("F:\\", "F:", "f:/"):
                script = b.build_command({"repo": "/home/me/3ds-platinum", "sd": p}, False, ["deploy"])[-1]
                self.assertNotIn("P3DS_SD", script, p)      # the exe copies to the card itself (sd_copy)
        finally:
            b.IS_WIN = old


class HeartGoldReady(unittest.TestCase):
    def _rom(self, code):
        import tempfile
        f = os.path.join(tempfile.mkdtemp(), "x.nds")
        with open(f, "wb") as h:
            h.write(b"POKEMON HG".ljust(12, b"\0") + code + b"\0" * 0x200)
        return f

    def test_hg_file_recognised_and_kept_out_of_other_boxes(self):
        hg, ss = self._rom(b"IPKE"), self._rom(b"IPGE")
        self.assertFalse(b.rom_report(hg, "heartgold")[0])      # US HeartGold but not the exact dump: the build would stop
        old = b.HG_SHA1
        b.HG_SHA1 = b._sha1(hg)
        try:
            self.assertTrue(b.rom_report(hg, "heartgold")[0])
        finally:
            b.HG_SHA1 = old
        self.assertFalse(b.rom_report(ss, "heartgold")[0])
        self.assertIn("SoulSilver box", b.rom_report(ss, "heartgold")[1])
        self.assertFalse(b.rom_report(hg, "soulsilver")[0])
        self.assertIn("HeartGold box", b.rom_report(hg, "soulsilver")[1])
        self.assertFalse(b.rom_report(hg, "platinum")[0])

    def test_hg_is_a_separate_game(self):
        g, ss = b.GAMES["heartgold"], b.GAMES["soulsilver"]
        self.assertEqual(g["script"], "hg_build.sh")
        self.assertNotEqual(g["repo"], ss["repo"])                     # its own project folder setting
        self.assertFalse(set(g["folders"]) & set(ss["folders"]))       # and its own clone
        self.assertEqual(b.game_out_dir("/h/x", "soulsilver"), "/h/x/soulsilver3ds/full")
        self.assertEqual(b.game_out_dir("/h/y", "heartgold"), "/h/y/heartgold3ds/full")
        for a, c in zip(b.SD_LAYOUT["heartgold"], b.SD_LAYOUT["soulsilver"]):
            self.assertNotEqual(a, c)                                  # no SD folder, program, .cia, ROM or save name shared
        self.assertEqual(b.selected_games({"want_ss": True, "want_hg": True}), ["soulsilver", "heartgold"])
        script = b.build_command({"hgrepo": "/h/y", "ssrepo": "/h/x", "heartgold": "/r/hg.nds"}, False, ["check"], game="heartgold")[-1]
        self.assertIn("P3DS_ROOT='/h/y'", script.replace('"', "'"))
        self.assertIn("P3DS_HEARTGOLD", script)
        self.assertIn("hg_build.sh", script)

    def test_hg_copy_never_touches_soulsilver(self):
        import tempfile
        t = tempfile.mkdtemp()
        out, sd = os.path.join(t, "out"), os.path.join(t, "F")
        os.makedirs(os.path.join(out, "HeartGold")); os.makedirs(os.path.join(sd, "3ds", "soulsilver3ds"))
        open(os.path.join(out, "HeartGold", "heartgold3ds.3dsx"), "wb").write(b"hg")
        ssd = os.path.join(sd, "3ds", "soulsilver3ds")
        for n in ("soulsilver.sav", "soulsilver3ds.3dsx", "soulsilver.nds"):
            open(os.path.join(ssd, n), "wb").write(b"SS" + n.encode())
        before = {n: open(os.path.join(ssd, n), "rb").read() for n in os.listdir(ssd)}
        rom = os.path.join(t, "hg.nds"); open(rom, "wb").write(b"rom")
        ok, msg, d = b.sd_copy("heartgold", out, sd, rom, log=lambda *_: None)
        self.assertTrue(ok, msg)
        self.assertEqual(os.path.normpath(d), os.path.join(sd, "3ds", "heartgold3ds"))
        self.assertEqual(sorted(os.listdir(d)), ["heartgold.nds", "heartgold3ds.3dsx"])
        self.assertEqual({n: open(os.path.join(ssd, n), "rb").read() for n in os.listdir(ssd)}, before)


class SdCopy(unittest.TestCase):
    def test_copy_keeps_save_and_backs_it_up(self):
        import tempfile
        t = tempfile.mkdtemp()
        out, sd = os.path.join(t, "out"), os.path.join(t, "F")
        os.makedirs(os.path.join(out, "Platinum")); os.makedirs(os.path.join(sd, "3ds", "platinum3ds"))
        for n, data in (("platinum3ds_full.3dsx", b"new"), ("platinum3ds.cia", b"cia"), ("followers.bin", b"fb")):
            open(os.path.join(out, "Platinum", n), "wb").write(data)
        g = os.path.join(sd, "3ds", "platinum3ds")
        open(os.path.join(g, "platinum.sav"), "wb").write(b"SAVE")
        open(os.path.join(g, "platinum3ds_full.3dsx"), "wb").write(b"old")
        rom = os.path.join(t, "pt.nds"); open(rom, "wb").write(b"rom")
        ok, msg, d = b.sd_copy("platinum", out, sd, rom, log=lambda *_: None)
        self.assertTrue(ok, msg)
        self.assertEqual(open(os.path.join(g, "platinum.sav"), "rb").read(), b"SAVE")
        self.assertEqual(len(os.listdir(os.path.join(g, "save_backups"))), 1)
        self.assertEqual(open(os.path.join(g, "platinum3ds_full.3dsx"), "rb").read(), b"new")
        self.assertEqual(open(os.path.join(g, "platinum3ds_full.3dsx.prev"), "rb").read(), b"old")
        self.assertTrue(os.path.isfile(os.path.join(g, "platinum.nds")))
        self.assertTrue(os.path.isfile(os.path.join(g, "followers.bin")))
        self.assertTrue(os.path.isfile(os.path.join(sd, "cias", "platinum3ds.cia")))
        ok, msg, _ = b.sd_copy("platinum", out, os.path.join(t, "nope"), rom, log=lambda *_: None)
        self.assertFalse(ok)
        self.assertIn("not there", msg)


class OutputFolder(unittest.TestCase):
    def test_out_exported(self):
        old = b.IS_WIN
        b.IS_WIN = True
        try:
            cmd = b.build_command({"repo": "/home/me/3ds-platinum", "out": "C:\\Users\\me\\Documents\\Pokemon 3DS Builds"}, False, ["deploy"])
            self.assertIn("export P3DS_OUT='/mnt/c/Users/me/Documents/Pokemon 3DS Builds'", cmd[-1])
        finally:
            b.IS_WIN = old


class OutDirs(unittest.TestCase):
    def test_out_dir(self):
        self.assertEqual(b.game_out_dir("/home/monke/3ds-platinum", "platinum"), "/home/monke/3ds-platinum/platinum3ds/full")
        self.assertEqual(b.game_out_dir("/home/monke/3ds-platinum/", "platinum"), "/home/monke/3ds-platinum/platinum3ds/full")
        self.assertEqual(b.game_out_dir("/home/monke/Pokemon-Soulsilver-New3ds", "soulsilver"),
                         "/home/monke/Pokemon-Soulsilver-New3ds/soulsilver3ds/full")
        self.assertEqual(b.game_out_dir("C:\\Users\\me\\3ds-platinum\\", "platinum"), "C:\\Users\\me\\3ds-platinum\\platinum3ds\\full")
        self.assertEqual(b.game_out_dir("\\\\wsl.localhost\\Ubuntu\\home\\me\\3ds-platinum", "platinum"),
                         "\\\\wsl.localhost\\Ubuntu\\home\\me\\3ds-platinum\\platinum3ds\\full")


if __name__ == "__main__":
    unittest.main()
