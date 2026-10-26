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

    def test_sd_reaches_build_command(self):
        old = b.IS_WIN
        b.IS_WIN = True
        try:
            for p in ("F:\\", "F:", "f:/"):
                cmd = b.build_command({"repo": "/home/me/3ds-platinum", "sd": p, "platinum": "C:\\roms\\pt.nds"}, False, ["deploy"])
                script = cmd[-1]
                self.assertIn("export P3DS_SD='/mnt/f/'", script, p)
                self.assertIn("export P3DS_SD_WANTED=1", script)
                self.assertIn("-u root", " ".join(b.sd_mount_command(p)))
        finally:
            b.IS_WIN = old


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
