import json
import shlex
import subprocess
import unittest

from fsol import cli, doctor
from fsol.doctor import Check

LONG = "Text in add-on windows can come out thin and jagged: " + "; ".join(f"Font {n} is replaced by Times New Roman" for n in range(6))


def report(*checks):
    return cli.doctor_text(list(checks)).splitlines()


class DoctorTextTest(unittest.TestCase):
    def test_the_summary_counts_each_group(self):
        lines = report(Check("a", "ok", "fine"), Check("b", "warn", "odd", "do this"), Check("c", "info", "note"), Check("d", "ok", "fine too"))
        self.assertEqual(lines[0], "Needs attention: 1. Good to know: 1. Fine: 2.")

    def test_problems_come_before_notes_and_notes_before_the_rest(self):
        lines = report(Check("a", "ok", "all good"), Check("b", "info", "a note"), Check("c", "fail", "broken", "repair it"))
        order = [line for line in lines if line in ("Needs attention", "Good to know", "Fine")]
        self.assertEqual(order, ["Needs attention", "Good to know", "Fine"])
        self.assertLess(lines.index("FAIL  broken"), lines.index("info  a note"))
        self.assertLess(lines.index("info  a note"), lines.index("ok    all good"))

    def test_each_heading_is_underlined(self):
        lines = report(Check("a", "ok", "all good"))
        self.assertEqual(lines[lines.index("Fine") + 1], "----")

    def test_an_empty_group_has_no_heading(self):
        lines = report(Check("a", "ok", "all good"))
        self.assertNotIn("Needs attention", lines)
        self.assertNotIn("Good to know", lines)

    def test_long_text_wraps_under_the_text_and_not_under_the_mark(self):
        lines = report(Check("fonts", "warn", LONG, "Run this with the simulator closed:"))
        body = lines[lines.index("Needs attention") + 3:]
        self.assertTrue(body[0].startswith("WARN  Text in add-on windows"))
        self.assertTrue(all(len(line) <= cli.DOCTOR_WIDTH for line in body))
        self.assertTrue(body[1].startswith("      ") and not body[1].startswith("       "))

    def test_a_path_longer_than_the_width_stays_whole(self):
        path = "/home/someone/" + "x" * 90 + "/MSFS2024"
        lines = report(Check("simulator", "ok", f"Installed in {path}"))
        self.assertTrue(any(line.strip() == path for line in lines))

    def test_a_command_stands_on_its_own_lines_and_is_never_wrapped(self):
        command = "protontricks -c 'wine reg add \"HKCU\\Software\\Microsoft\\Avalon.Graphics\" /v DisableHWAcceleration /t REG_DWORD /d 1 /f' 2537590"
        lines = report(Check("wpf", "warn", "WPF draws with Direct3D 9", "Run this with the simulator closed:", command))
        self.assertIn("        " + command, lines)
        self.assertEqual(lines[lines.index("        " + command) - 1], "")

    def test_a_check_that_is_fine_shows_no_fix_or_command(self):
        lines = report(Check("wpf", "ok", "WPF draws in software", "never shown", "never run"))
        self.assertNotIn("never shown", "\n".join(lines))
        self.assertNotIn("never run", "\n".join(lines))

    def test_json_carries_the_command_apart_from_the_fix(self):
        check = Check("wpf", "warn", "detail", "Run this:", "do-it")
        self.assertEqual(json.loads(json.dumps(check.as_dict()))["command"], "do-it")


class FontCommandTest(unittest.TestCase):
    FIXES = [("Segoe UI", "Noto Sans"), ("Verdana", "DejaVu Sans")]

    def test_each_registry_change_has_its_own_short_line(self):
        lines = doctor.font_command(self.FIXES).splitlines()
        self.assertIn('"Segoe UI"', lines[1])
        self.assertIn('"Verdana"', lines[2])
        self.assertTrue(all(len(line) + len(cli.COMMAND_INDENT) <= cli.DOCTOR_WIDTH for line in lines))

    def test_the_lines_make_one_protontricks_call_whose_script_writes_each_replacement(self):
        words = shlex.split(doctor.font_command(self.FIXES))
        self.assertEqual(words[:2], ["protontricks", "-c"])
        self.assertEqual(words[3:], ["2537590"])
        fake_wine = 'wine() { printf "%s|" "$@"; echo; }; '
        written = subprocess.run(["sh", "-c", fake_wine + words[2]], capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(written, [
            'reg|add|HKCU\\Software\\Wine\\Fonts\\Replacements|/v|Segoe UI|/d|Noto Sans|/f|',
            'reg|add|HKCU\\Software\\Wine\\Fonts\\Replacements|/v|Verdana|/d|DejaVu Sans|/f|',
        ])


if __name__ == "__main__":
    unittest.main()
