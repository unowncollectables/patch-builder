"""Black 2 sprites box: sprites-only plans and file checks (no GUI, no WSL needed)."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import p3ds_builder as b


def cfg(**kw):
    c = {k: "" for k in b.FIELDS}
    c.update(want_plat=False, want_ss=False, want_hg=False, want_pb=False, want_b2=False, want_w2=False, want_b2spr=False)
    c.update(kw)
    return c


class B2Sprites(unittest.TestCase):
    def test_sprites_only_plan(self):
        c = cfg(want_b2spr=True, black2="/r/b2.nds")
        self.assertTrue(b.sprites_only(c))
        self.assertEqual(b.run_games(c), ["soulsilver"])
        self.assertEqual(b.plan_for("soulsilver", "auto", c, ["full"]), [(False, ["b2sprites"])])
        self.assertEqual(b.plan_for("soulsilver", "auto", c, ["setup"]), [(True, ["setup"]), (False, ["b2sprites"])])
        self.assertEqual(b.chosen_roms(c), [("/r/b2.nds", "black2", False)])

    def test_white2_file(self):
        c = cfg(want_b2spr=True, white2="/r/w2.nds")
        self.assertEqual(b.chosen_roms(c), [("/r/w2.nds", "white2", False)])

    def test_with_soulsilver(self):
        c = cfg(want_b2spr=True, want_ss=True, soulsilver="/r/ss.nds", black2="/r/b2.nds")
        self.assertFalse(b.sprites_only(c))
        self.assertEqual(b.run_games(c), ["soulsilver"])
        self.assertIn(("/r/b2.nds", "black2", False), b.chosen_roms(c))
        self.assertNotIn(["b2sprites"], [s for _, s in b.plan_for("soulsilver", "auto", c, [])])
        self.assertIn("export P3DS_B2SPRITES=1", b.build_command(c, False, ["build"], game="soulsilver")[-1])

    def test_untick_means_no_sprites(self):
        c = cfg(want_ss=True, soulsilver="/r/ss.nds", black2="/r/b2.nds")
        self.assertNotIn("P3DS_B2SPRITES", b.build_command(c, False, ["build"], game="soulsilver")[-1])
        self.assertEqual(b.run_games(cfg()), [])


if __name__ == "__main__":
    unittest.main()
