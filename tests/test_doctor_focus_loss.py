import tempfile
import unittest
from pathlib import Path

from fsol import doctor
from tests.test_machine import LOCALCONFIG, FakeMachine

WAYLAND = LOCALCONFIG.replace("%command%", "PROTON_ENABLE_WAYLAND=1 %command%")
KEPT = LOCALCONFIG.replace("%command%", "PROTON_ENABLE_WAYLAND=1 WAYLANDDRV_FOCUS_LOSS=0 %command%")


class FocusLossTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = FakeMachine(Path(self.temp.name))

    def focus_loss(self, launch_options):
        found = self.fake.build(launch_options=launch_options).discover()
        return next((check for check in doctor.run(found) if check.name == "focus-loss"), None)

    def test_under_the_wayland_driver_the_doctor_offers_to_keep_the_focus(self):
        check = self.focus_loss(WAYLAND)
        self.assertEqual(check.status, "info")
        self.assertIn("frame generation turns off", check.detail)
        self.assertIn("WAYLANDDRV_FOCUS_LOSS=0 before %command%", check.fix)

    def test_with_the_option_set_the_doctor_says_frame_generation_stays_on(self):
        check = self.focus_loss(KEPT)
        self.assertEqual(check.status, "info")
        self.assertIn("WAYLANDDRV_FOCUS_LOSS=0", check.detail)
        self.assertIn("stays the foreground window", check.detail)
        self.assertEqual(check.fix, "")

    def test_a_value_other_than_zero_leaves_focus_loss_on(self):
        check = self.focus_loss(KEPT.replace("WAYLANDDRV_FOCUS_LOSS=0", "WAYLANDDRV_FOCUS_LOSS=1"))
        self.assertIn("WAYLANDDRV_FOCUS_LOSS=0 before %command%", check.fix)

    def test_under_the_x11_driver_the_option_does_not_apply(self):
        self.assertIsNone(self.focus_loss(LOCALCONFIG))


if __name__ == "__main__":
    unittest.main()
