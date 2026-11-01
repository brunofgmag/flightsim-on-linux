import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fsol import addons, desktop
from tests.test_machine import FakeMachine, write

SPEC = {
    "name": "Demo Add-on",
    "tested_version": "1.2.3",
    "tested_on": "2026-10-17",
    "needs": [],
    "touches_addon_files": False,
    "installed": {"community": ["demo-addon-*"]},
    "shortcuts": [{"title": "Demo", "icon": {"place": "prefix", "glob": "drive_c/Demo/demo.exe"}}],
}
CLI = Path("/opt/flightsim on linux/bin/flightsim-on-linux")
HAVE_ICON_TOOLS = bool(shutil.which("7z") and shutil.which("magick") and shutil.which("x86_64-w64-mingw32-gcc") and shutil.which("x86_64-w64-mingw32-windres"))


class Recorder(desktop.Tools):
    def __init__(self, missing=(), real=()):
        self.missing = set(missing)
        self.real = set(real)
        self.calls = []

    def which(self, name):
        return None if name in self.missing else (shutil.which(name) or f"/usr/bin/{name}")

    def run(self, argv):
        self.calls.append(argv)
        if argv[0] in self.real:
            return super().run(argv)
        return 0


def make_addon(root, spec=None, scripts=("launch", "close")):
    folder = root / "addons" / "demo"
    write(folder / "addon.json", json.dumps(spec or SPEC))
    for name in ("apply", "check", "undo", *scripts):
        write(folder / name, "#!/bin/sh\n")
        (folder / name).chmod(0o755)
    return addons.load(folder)


def windows_program_with_icon(folder):
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["magick", "-size", "64x64", "xc:#cc3300", "-fill", "white", "-draw", "circle 32,32 32,8", "-define", "icon:auto-resize=64,32,16", str(folder / "app.ico")], check=True)
    (folder / "app.rc").write_text('1 ICON "app.ico"\n')
    (folder / "main.c").write_text("int main(void) { return 0; }\n")
    subprocess.run(["x86_64-w64-mingw32-windres", "app.rc", "app.o"], cwd=folder, check=True)
    subprocess.run(["x86_64-w64-mingw32-gcc", "-o", "demo.exe", "main.c", "app.o"], cwd=folder, check=True)
    return folder / "demo.exe"


class DesktopCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.data = self.root / "data"
        self.said = []

    def tearDown(self):
        self.temp.cleanup()

    def write(self, addon, tools=None):
        return desktop.write_shortcuts(addon, self.found, CLI, self.data, tools or Recorder(), self.said.append)

    def entry(self, name="flightsim-on-linux-demo.desktop"):
        return (self.data / "applications" / name).read_text()

    def lines(self, name="flightsim-on-linux-demo.desktop"):
        return self.entry(name).splitlines()


class EntryTest(DesktopCase):
    def test_one_shortcut_writes_one_entry_named_after_the_addon(self):
        written = self.write(make_addon(self.root))
        self.assertEqual(written, [self.data / "applications" / "flightsim-on-linux-demo.desktop"])
        self.assertEqual(self.lines()[:3], ["[Desktop Entry]", "Type=Application", "Name=Demo"])

    def test_more_shortcuts_are_numbered_from_the_second(self):
        spec = dict(SPEC, shortcuts=[{"title": "First"}, {"title": "Second"}, {"title": "Third"}])
        written = self.write(make_addon(self.root, spec))
        self.assertEqual([path.name for path in written], ["flightsim-on-linux-demo.desktop", "flightsim-on-linux-demo-2.desktop", "flightsim-on-linux-demo-3.desktop"])
        self.assertIn("Name=Third", self.lines("flightsim-on-linux-demo-3.desktop"))

    def test_exec_is_the_quoted_absolute_cli_calling_addon_launch(self):
        self.write(make_addon(self.root))
        self.assertIn('Exec="/opt/flightsim on linux/bin/flightsim-on-linux" addon launch demo launch', self.lines())

    def test_arguments_follow_a_double_dash_and_quoting_survives_reserved_characters(self):
        spec = dict(SPEC, shortcuts=[{"title": "Demo", "script": "launch", "args": ["--minimized", 'say "hi" $HOME `x` 100%\\']}])
        self.write(make_addon(self.root, spec))
        exec_line = next(line for line in self.lines() if line.startswith("Exec="))
        self.assertTrue(exec_line.endswith(r' -- "--minimized" "say \\"hi\\" \\$HOME \\`x\\` 100%%\\\\"'), exec_line)

    def test_mime_adds_the_url_field_code_and_the_mime_type(self):
        spec = dict(SPEC, shortcuts=[{"title": "Demo", "mime": ["x-scheme-handler/demo", "x-scheme-handler/other"]}])
        self.write(make_addon(self.root, spec))
        lines = self.lines()
        self.assertTrue(any(line.endswith(" addon launch demo launch -- %u") for line in lines))
        self.assertIn("MimeType=x-scheme-handler/demo;x-scheme-handler/other;", lines)

    def test_without_mime_there_is_no_field_code_and_no_mime_type(self):
        self.write(make_addon(self.root))
        self.assertNotIn("%u", self.entry())
        self.assertNotIn("MimeType", self.entry())

    def test_a_close_script_becomes_a_desktop_action(self):
        spec = dict(SPEC, shortcuts=[{"title": "Demo", "close": "close"}])
        self.write(make_addon(self.root, spec))
        lines = self.lines()
        self.assertIn("Actions=close;", lines)
        group = lines[lines.index("[Desktop Action close]"):]
        self.assertEqual(group[1], "Name=Close Demo")
        self.assertTrue(group[2].endswith(" addon launch demo close"))

    def test_the_fixed_fields_and_an_english_comment(self):
        self.write(make_addon(self.root))
        lines = self.lines()
        for expected in ("Categories=Game;", "Terminal=false", "Comment=Demo, set up by the Demo Add-on add-on", "X-FlightsimOnLinux-Addon=demo"):
            self.assertIn(expected, lines)

    def test_the_comment_of_the_shortcut_wins(self):
        spec = dict(SPEC, shortcuts=[{"title": "Demo", "comment": "Does a thing"}])
        self.write(make_addon(self.root, spec))
        self.assertIn("Comment=Does a thing", self.lines())

    def test_the_desktop_database_is_refreshed_and_a_missing_one_is_ignored(self):
        recorder = Recorder()
        self.write(make_addon(self.root), recorder)
        self.assertEqual(recorder.calls, [["update-desktop-database", str(self.data / "applications")], ["kbuildsycoca6"]])
        bare = Recorder(missing=("update-desktop-database", "kbuildsycoca6"))
        self.write(make_addon(self.root), bare)
        self.assertEqual(bare.calls, [])

    def test_writing_again_drops_the_entries_a_shorter_list_no_longer_has(self):
        self.write(make_addon(self.root, dict(SPEC, shortcuts=[{"title": "A"}, {"title": "B"}])))
        self.write(make_addon(self.root, dict(SPEC, shortcuts=[{"title": "A"}])))
        self.assertEqual(sorted(path.name for path in (self.data / "applications").iterdir()), ["flightsim-on-linux-demo.desktop"])


class RemoveTest(DesktopCase):
    def test_removing_deletes_the_entries_and_icons_of_that_addon_only(self):
        demo = make_addon(self.root, dict(SPEC, shortcuts=[{"title": "A"}, {"title": "B"}]))
        self.write(demo)
        icon = desktop.icon_path(demo, 0, self.data)
        write(icon, "png")
        other = self.root / "other" / "demo-textures"
        write(other / "addon.json", json.dumps(dict(SPEC, shortcuts=[{"title": "Textures"}])))
        for name in ("apply", "check", "undo", "launch"):
            write(other / name, "#!/bin/sh\n")
            (other / name).chmod(0o755)
        textures = addons.load(other)
        self.write(textures)
        removed = desktop.remove_shortcuts(demo, self.data, Recorder())
        self.assertEqual(sorted(path.name for path in removed), ["flightsim-on-linux-demo-2.desktop", "flightsim-on-linux-demo.desktop", "flightsim-on-linux-demo.png"])
        self.assertEqual(sorted(path.name for path in (self.data / "applications").iterdir()), ["flightsim-on-linux-demo-textures.desktop"])

    def test_a_file_with_a_similar_name_but_another_marker_is_left_alone(self):
        demo = make_addon(self.root)
        write(self.data / "applications" / "flightsim-on-linux-demo-2.desktop", "[Desktop Entry]\nX-FlightsimOnLinux-Addon=demo-2\n")
        self.write(demo)
        desktop.remove_shortcuts(demo, self.data, Recorder())
        self.assertTrue((self.data / "applications" / "flightsim-on-linux-demo-2.desktop").exists())

    def test_removing_when_nothing_was_written_changes_nothing_and_refreshes_nothing(self):
        recorder = Recorder()
        self.assertEqual(desktop.remove_shortcuts(make_addon(self.root), self.data, recorder), [])
        self.assertEqual(recorder.calls, [])

    def test_has_shortcuts_needs_every_entry_on_disk(self):
        demo = make_addon(self.root, dict(SPEC, shortcuts=[{"title": "A"}, {"title": "B"}]))
        self.assertFalse(desktop.has_shortcuts(demo, self.data))
        self.write(demo)
        self.assertTrue(desktop.has_shortcuts(demo, self.data))
        (self.data / "applications" / "flightsim-on-linux-demo-2.desktop").unlink()
        self.assertFalse(desktop.has_shortcuts(demo, self.data))

    def test_an_addon_without_shortcuts_has_none(self):
        spec = {key: value for key, value in SPEC.items() if key != "shortcuts"}
        self.assertFalse(desktop.has_shortcuts(make_addon(self.root, spec), self.data))


class IconTest(DesktopCase):
    def program(self, content=b"MZ"):
        path = self.found.prefix / "drive_c" / "Demo" / "demo.exe"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def assert_no_icon(self, tools, reason):
        self.write(make_addon(self.root), tools)
        self.assertNotIn("Icon=", self.entry())
        self.assertEqual(len(self.said), 1)
        self.assertIn(reason, self.said[0])

    def test_without_7z_the_entry_has_no_icon_and_one_line_says_why(self):
        self.program()
        self.assert_no_icon(Recorder(missing=("7z",)), "7z is not installed")

    def test_without_magick_the_entry_has_no_icon_and_one_line_says_why(self):
        self.program()
        self.assert_no_icon(Recorder(missing=("magick",)), "magick is not installed")

    def test_no_file_matching_the_glob_leaves_no_icon(self):
        self.assert_no_icon(Recorder(), "nothing in the prefix folder matches drive_c/Demo/demo.exe")

    def test_an_appimage_leaves_no_icon(self):
        appimage = self.found.prefix / "drive_c" / "Demo" / "demo.AppImage"
        appimage.parent.mkdir(parents=True)
        appimage.write_bytes(b"\x7fELF")
        spec = dict(SPEC, shortcuts=[{"title": "Demo", "icon": {"place": "prefix", "glob": "drive_c/Demo/*.AppImage"}}])
        self.write(make_addon(self.root, spec), Recorder())
        self.assertNotIn("Icon=", self.entry())
        self.assertIn("AppImage", self.said[0])

    def test_a_shortcut_that_names_no_icon_is_written_without_failing(self):
        self.write(make_addon(self.root, dict(SPEC, shortcuts=[{"title": "Demo"}])))
        self.assertNotIn("Icon=", self.entry())
        self.assertEqual(len(self.said), 1)

    def test_a_program_without_icon_resources_leaves_no_icon(self):
        self.program()
        self.assert_no_icon(Recorder(real=("7z", "magick")), "has no icon resources")
        self.assertFalse((self.data / "icons").exists())

    @unittest.skipUnless(HAVE_ICON_TOOLS, "needs 7z, magick and mingw")
    def test_the_largest_icon_of_a_real_program_is_extracted_and_never_enlarged(self):
        built = windows_program_with_icon(self.root / "build")
        shutil.copy(built, self.program())
        self.write(make_addon(self.root), Recorder(real=("7z", "magick")))
        icon = self.data / "icons" / "hicolor" / "256x256" / "apps" / "flightsim-on-linux-demo.png"
        self.assertIn(f"Icon={icon}", self.lines())
        self.assertEqual(self.said, [])
        size = subprocess.run(["magick", "identify", "-format", "%wx%h", str(icon)], capture_output=True, text=True, check=True).stdout
        self.assertEqual(size, "64x64")


@unittest.skipUnless(shutil.which("desktop-file-validate"), "needs desktop-file-validate")
class ValidateTest(DesktopCase):
    def validate(self, written):
        for path in written:
            result = subprocess.run(["desktop-file-validate", str(path)], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""), path.name)

    def test_every_generated_file_passes_desktop_file_validate(self):
        spec = dict(SPEC, shortcuts=[
            {"title": "Plain"},
            {"title": "With close", "close": "close", "comment": "Back\\slash"},
            {"title": "Scheme", "mime": ["x-scheme-handler/demo"], "args": ["--minimized"]},
            {"title": "Odd args", "args": ['say "hi" $HOME `x` 100%\\', "two words"]},
        ])
        self.validate(self.write(make_addon(self.root, spec)))

    @unittest.skipUnless(HAVE_ICON_TOOLS, "needs 7z, magick and mingw")
    def test_an_entry_with_a_real_icon_passes_desktop_file_validate(self):
        built = windows_program_with_icon(self.root / "build")
        target = self.found.prefix / "drive_c" / "Demo" / "demo.exe"
        target.parent.mkdir(parents=True)
        shutil.copy(built, target)
        self.validate(self.write(make_addon(self.root), Recorder(real=("7z", "magick"))))

    def test_a_cli_path_with_spaces_and_quotes_passes_desktop_file_validate(self):
        path = desktop.write_shortcuts(make_addon(self.root), self.found, Path('/opt/odd "dir"/$x/flightsim-on-linux'), self.data, Recorder(), self.said.append)
        self.validate(path)


if __name__ == "__main__":
    unittest.main()
