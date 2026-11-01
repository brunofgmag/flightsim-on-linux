import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fsol import exe_xml

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "exe-entry.py"
SUFFIX = "\\data\\tool\\plugin.exe"


def entry(path, disabled="false", name="Plugin"):
    return f"    <Launch.Addon>\n        <Name>{name}</Name>\n        <Disabled>{disabled}</Disabled>\n        <Path>{path}</Path>\n    </Launch.Addon>\n"


def document(*entries):
    return '<?xml version="1.0" encoding="windows-1252"?>\n<SimBase.Document Type="Launch" version="1,0">\n' + "".join(entries) + "</SimBase.Document>\n"


class FileCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "exe.xml"

    def put(self, text):
        self.file.write_text(text)


class StateEndingTest(FileCase):
    def test_a_missing_file_has_no_state(self):
        self.assertEqual(exe_xml.state_ending(self.file, SUFFIX), ("no-file", None))

    def test_an_entry_is_found_by_the_end_of_its_path_ignoring_case(self):
        self.put(document(entry("C:\\Games\\IFLY\\DATA\\TOOL\\PLUGIN.EXE")))
        self.assertEqual(exe_xml.state_ending(self.file, SUFFIX), ("enabled", "C:\\Games\\IFLY\\DATA\\TOOL\\PLUGIN.EXE"))

    def test_other_paths_are_absent(self):
        self.put(document(entry("C:\\Other\\other.exe")))
        self.assertEqual(exe_xml.state_ending(self.file, SUFFIX), ("absent", None))

    def test_a_disabled_entry_is_disabled(self):
        self.put(document(entry("C:\\Games\\data\\tool\\plugin.exe", disabled="True")))
        self.assertEqual(exe_xml.state_ending(self.file, SUFFIX), ("disabled", "C:\\Games\\data\\tool\\plugin.exe"))

    def test_one_enabled_entry_among_disabled_ones_is_enough(self):
        self.put(document(entry("C:\\A\\data\\tool\\plugin.exe", disabled="true"), entry("D:\\B\\data\\tool\\plugin.exe")))
        self.assertEqual(exe_xml.state_ending(self.file, SUFFIX), ("enabled", "D:\\B\\data\\tool\\plugin.exe"))

    def test_a_commented_out_entry_does_not_count(self):
        self.put(document("<!--" + entry("C:\\Games\\data\\tool\\plugin.exe") + "-->"))
        self.assertEqual(exe_xml.state_ending(self.file, SUFFIX), ("absent", None))

    def test_a_path_with_xml_escapes_is_read_as_written(self):
        self.put(document(entry("C:\\Fish &amp; Chips\\data\\tool\\plugin.exe")))
        self.assertEqual(exe_xml.state_ending(self.file, SUFFIX)[1], "C:\\Fish & Chips\\data\\tool\\plugin.exe")

    def test_an_added_entry_escapes_the_xml_in_its_name_and_path(self):
        text = exe_xml.add_entry(document(), "Fish & <Chips>", "C:\\Fish & Chips\\data\\tool\\plugin.exe")
        self.assertIn("<Name>Fish &amp; &lt;Chips&gt;</Name>", text)
        self.assertIn("<Path>C:\\Fish &amp; Chips\\data\\tool\\plugin.exe</Path>", text)
        self.put(text)
        self.assertEqual(exe_xml.state_ending(self.file, SUFFIX), ("enabled", "C:\\Fish & Chips\\data\\tool\\plugin.exe"))

    def test_a_utf16_file_is_refused(self):
        self.file.write_bytes(b"\xff\xfe<\x00")
        with self.assertRaises(exe_xml.ExeXmlError):
            exe_xml.state_ending(self.file, SUFFIX)


class SwitchTest(FileCase):
    def switch(self, command, suffix=SUFFIX):
        result = subprocess.run([sys.executable, "-I", str(ROOT / "fsol" / "exe_xml.py"), command, str(self.file), suffix], capture_output=True, text=True)
        return result.returncode, result.stdout.strip()

    def test_disable_turns_the_entry_off_and_keeps_every_other_byte(self):
        kept = entry("C:\\Other\\other.exe", name="Other")
        self.put(document(kept, entry("C:\\Games\\data\\tool\\plugin.exe", "False")))
        self.assertEqual(self.switch("disable"), (0, "disabled"))
        self.assertEqual(self.file.read_text(), document(kept, entry("C:\\Games\\data\\tool\\plugin.exe", "True")))

    def test_disable_twice_says_so(self):
        self.put(document(entry("C:\\Games\\data\\tool\\plugin.exe", "True")))
        self.assertEqual(self.switch("disable"), (0, "already"))

    def test_enable_turns_the_entry_back_on(self):
        self.put(document(entry("C:\\Games\\data\\tool\\plugin.exe", "True")))
        self.assertEqual(self.switch("enable"), (0, "enabled"))
        self.assertEqual(self.file.read_text(), document(entry("C:\\Games\\data\\tool\\plugin.exe", "False")))

    def test_an_entry_with_no_disabled_tag_gets_one(self):
        self.put(document("    <Launch.Addon>\n        <Name>Plugin</Name>\n        <Path>C:\\Games\\data\\tool\\plugin.exe</Path>\n    </Launch.Addon>\n"))
        self.assertEqual(self.switch("disable"), (0, "disabled"))
        self.assertEqual(exe_xml.state_ending(self.file, SUFFIX)[0], "disabled")

    def test_an_entry_inside_a_comment_is_left_alone(self):
        text = document("<!--\n" + entry("C:\\Games\\data\\tool\\plugin.exe") + "-->\n")
        self.put(text)
        self.assertEqual(self.switch("disable"), (0, "absent"))
        self.assertEqual(self.file.read_text(), text)

    def test_a_commented_out_copy_stays_as_it_is_when_the_real_entry_changes(self):
        commented = "<!--\n" + entry("C:\\Games\\data\\tool\\plugin.exe", "False", "Old") + "-->\n"
        self.put(document(commented, entry("C:\\Games\\data\\tool\\plugin.exe", "False")))
        self.assertEqual(self.switch("disable"), (0, "disabled"))
        self.assertEqual(self.file.read_text(), document(commented, entry("C:\\Games\\data\\tool\\plugin.exe", "True")))

    def test_a_missing_file_and_a_missing_entry_change_nothing(self):
        self.assertEqual(self.switch("disable"), (0, "no-file"))
        self.assertFalse(self.file.exists())
        self.put(document(entry("C:\\Other\\other.exe")))
        self.assertEqual(self.switch("enable"), (0, "absent"))


class LinuxPathTest(FileCase):
    def setUp(self):
        super().setUp()
        self.prefix = self.root / "pfx"
        (self.prefix / "drive_c/Games/iFly/Data").mkdir(parents=True)
        (self.prefix / "drive_c/Games/iFly/Data/Plugin.exe").write_text("")
        (self.prefix / "dosdevices").mkdir()
        self.other = self.root / "other-disk"
        (self.other / "Tools").mkdir(parents=True)
        (self.other / "Tools/App.exe").write_text("")
        os.symlink(self.other, self.prefix / "dosdevices/d:")

    def test_the_c_drive_works_without_a_dosdevices_link(self):
        self.assertEqual(exe_xml.linux_path(self.prefix, "C:\\Games\\iFly\\Data\\Plugin.exe"), self.prefix / "drive_c/Games/iFly/Data/Plugin.exe")

    def test_every_part_is_matched_ignoring_case(self):
        self.assertEqual(exe_xml.linux_path(self.prefix, "c:\\GAMES\\ifly\\DATA\\PLUGIN.EXE"), self.prefix / "drive_c/Games/iFly/Data/Plugin.exe")

    def test_another_drive_follows_its_dosdevices_link(self):
        self.assertEqual(exe_xml.linux_path(self.prefix, "D:\\tools\\app.exe"), self.other.resolve() / "Tools/App.exe")

    def test_a_file_that_is_not_there_is_none(self):
        self.assertIsNone(exe_xml.linux_path(self.prefix, "C:\\Games\\iFly\\Data\\Gone.exe"))

    def test_a_drive_with_no_link_is_none(self):
        self.assertIsNone(exe_xml.linux_path(self.prefix, "E:\\Tools\\App.exe"))

    def test_text_that_is_not_a_windows_path_is_none(self):
        self.assertIsNone(exe_xml.linux_path(self.prefix, "/home/pilot/App.exe"))
        self.assertIsNone(exe_xml.linux_path(self.prefix, "CD:\\App.exe"))


class ToolTest(FileCase):
    def setUp(self):
        super().setUp()
        self.prefix = self.root / "pfx"
        (self.prefix / "drive_c/Games/data/tool").mkdir(parents=True)
        (self.prefix / "drive_c/Games/data/tool/plugin.exe").write_text("")

    def run_tool(self, *args):
        result = subprocess.run([sys.executable, "-I", str(TOOL), *args], capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def test_state_prints_the_state_of_the_entry(self):
        self.assertEqual(self.run_tool("state", str(self.file), SUFFIX), (0, "no-file", ""))
        self.put(document(entry("C:\\Games\\data\\tool\\plugin.exe")))
        self.assertEqual(self.run_tool("state", str(self.file), SUFFIX), (0, "enabled", ""))

    def check(self):
        return self.run_tool("check", str(self.prefix), str(self.file), SUFFIX, "the plugin", "run: apply")

    def test_check_passes_when_the_entry_is_enabled_and_the_file_exists(self):
        self.put(document(entry("C:\\GAMES\\DATA\\TOOL\\PLUGIN.EXE")))
        self.assertEqual(self.check(), (0, "ok exe.xml starts the plugin with the simulator", ""))

    def test_check_fails_with_the_hint_when_there_is_no_file_or_no_entry(self):
        self.assertEqual(self.check()[:2], (1, f"FAIL exe.xml does not exist at {self.file}; run: apply"))
        self.put(document(entry("C:\\Other\\other.exe")))
        self.assertEqual(self.check()[:2], (1, "FAIL exe.xml has no entry for the plugin; run: apply"))

    def test_check_fails_for_a_disabled_entry(self):
        self.put(document(entry("C:\\Games\\data\\tool\\plugin.exe", disabled="true")))
        self.assertEqual(self.check()[:2], (1, "FAIL exe.xml has the entry for the plugin with Disabled true, so the simulator won't start it"))

    def test_check_fails_when_the_program_the_entry_names_is_gone(self):
        self.put(document(entry("C:\\Games\\data\\tool\\plugin.exe")))
        (self.prefix / "drive_c/Games/data/tool/plugin.exe").unlink()
        self.assertEqual(self.check()[:2], (1, "FAIL exe.xml starts C:\\Games\\data\\tool\\plugin.exe for the plugin, and that file does not exist"))

    def test_check_fails_for_a_file_it_cannot_read(self):
        self.file.write_bytes(b"\xff\xfe<\x00")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("FAIL exe.xml cannot be read: "))

    def test_a_wrong_number_of_arguments_prints_the_usage_and_exits_2(self):
        code, _, err = self.run_tool("state", str(self.file))
        self.assertEqual(code, 2)
        self.assertIn("usage: exe-entry.py", err)


if __name__ == "__main__":
    unittest.main()
