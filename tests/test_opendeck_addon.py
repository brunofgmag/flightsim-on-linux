import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fsol import addons
from tests.test_machine import write

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "opendeck"
DEVICE = "20GBD9901=sd-TEST0001"
SERIAL = "sd-TEST0001"
PROFILE = "11111111-1111-1111-1111-111111111111"
SECOND = "22222222-2222-2222-2222-222222222222"
PAGE_ONE = "aaaaaaaa-0000-0000-0000-000000000001"
PAGE_TWO = "aaaaaaaa-0000-0000-0000-000000000002"
FOLDER_PAGE = "bbbbbbbb-0000-0000-0000-000000000001"
SWITCH = "com.amansprojects.starterpack.switchprofile"
INPUT = "com.amansprojects.starterpack.inputsimulation"
REDLINE = "com.kahikara.opendeck-redline"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def stub(path, text):
    write(path, text)
    path.chmod(0o755)
    return path


def write_json(path, data):
    write(path, json.dumps(data))


def tree(root):
    return sorted(str(path.relative_to(root)) + (":" + sha256(path.read_bytes()) if path.is_file() and not path.is_symlink() else "") for path in root.rglob("*"))


def action(uuid, title="", settings=None):
    return {"UUID": uuid, "Settings": settings or {}, "States": [{"Title": title}]}


def plugin_manifest(*actions):
    return {"Actions": [{"UUID": uuid, "Name": uuid, "Icon": "icon", "Controllers": ["Keypad"], "States": [{"Image": "icon"}]} for uuid in actions]}


class ShapeTest(unittest.TestCase):
    def test_the_manifest_describes_a_native_program(self):
        addon = addons.load(ADDON)
        self.assertEqual((addon.id, addon.tested_version, addon.tested_on, addon.touches_addon_files, addon.needs), ("opendeck", "2.14.2", "2026-10-22", True, []))
        self.assertEqual(addon.install["kind"], "native")
        self.assertFalse(addon.can_install)
        self.assertEqual((addon.shortcuts, addon.watcher), ([], None))

    def test_the_three_source_patches_and_the_scripts_are_there(self):
        for name in ("swipe-changes-the-page.patch", "dial-title-like-elgato.patch", "strip-render-no-checkerboard.patch"):
            self.assertTrue((ADDON / "patches" / name).is_file(), name)
        for name in ("convert-profiles", "rebuild", "aao-plugin", "check", "apply", "undo"):
            self.assertTrue(os.access(ADDON / name, os.X_OK), name)

    def test_nothing_of_the_owner_is_in_the_add_on_or_its_shims(self):
        words = ("/home/brunom", "brunom", "Prefixos", "Jogos", "FlightAddons", "eno1", "nvidia:0")
        for folder in (ADDON, ROOT / "addons" / "reshade", ROOT / "addons" / "glass-cockpit-dlss-fix", ROOT / "shims" / "procgov", ROOT / "shims" / "winhttp-proxy", ROOT / "shims" / "oletr"):
            for path in folder.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    data = path.read_bytes()
                    for word in words:
                        self.assertNotIn(word.encode(), data, f"{path} holds {word}")
                    self.assertIsNone(re.search(rb"A00[A-Z]{2}[0-9]{4}", data), f"{path} holds a Stream Deck serial")

    def test_the_converter_and_its_messages_are_in_english(self):
        text = (ADDON / "parts" / "convert.py").read_text()
        self.assertIsNone(re.search(r"[ãõçáéíóúâêô]", text))


class ConverterCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "StreamDeck"
        self.config = self.root / "opendeck"
        self.report = self.root / "report.json"
        write_json(self.config / "plugins" / "com.amansprojects.starterpack.sdPlugin" / "manifest.json", plugin_manifest(SWITCH, INPUT))
        self.profile(PROFILE, "Fixture", [PAGE_ONE, PAGE_TWO])
        self.page(PROFILE, PAGE_ONE, {"0,0": action("com.elgato.streamdeck.page.next", "Next")})
        self.page(PROFILE, PAGE_TWO, {"0,0": action("com.elgato.streamdeck.page.next", "Next"), "1,0": action("com.elgato.streamdeck.system.hotkey", "Copy", {"Hotkeys": [{"VKeyCode": 67, "KeyModifiers": 2}]})})

    def tearDown(self):
        self.temp.cleanup()

    def profile(self, uuid, name, pages, model="20GBD9901"):
        write_json(self.source / "ProfilesV3" / f"{uuid.upper()}.sdProfile" / "manifest.json", {"Name": name, "Device": {"Model": model}, "Pages": {"Pages": [page.upper() for page in pages], "Default": pages[0].upper()}})

    def page(self, profile, page, actions, controller="Keypad"):
        write_json(self.source / "ProfilesV3" / f"{profile.upper()}.sdProfile" / "Profiles" / page.upper() / "manifest.json", {"Controllers": [{"Type": controller, "Actions": actions}]})

    def plugin(self, uuid, manifest, raw=None):
        folder = self.source / "Plugins" / f"{uuid}.sdPlugin"
        if raw is None:
            write_json(folder / "manifest.json", manifest)
        else:
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_bytes(raw)

    def convert(self, *extra, devices=(DEVICE,)):
        argv = [str(ADDON / "convert-profiles"), "--source", str(self.source), "--config", str(self.config), "--report", str(self.report)]
        for device in devices:
            argv += ["--device", device]
        return subprocess.run(argv + list(extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def written(self, *parts):
        return json.loads(self.config.joinpath("profiles", SERIAL, *parts).read_text())

    def reported(self):
        return json.loads(self.report.read_text())


class ConverterTest(ConverterCase):
    def test_pages_become_profiles_named_page_nn_in_a_group_with_the_profile_name(self):
        result = self.convert()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(path.name for path in (self.config / "profiles" / SERIAL / "Fixture").iterdir()), ["Page 01.json", "Page 02.json"])

    def test_a_stream_deck_plus_gets_eight_keys_and_four_dials(self):
        self.convert()
        page = self.written("Fixture", "Page 01.json")
        self.assertEqual((len(page["keys"]), len(page["sliders"]), page["infobars"]), (8, 4, []))

    def test_next_page_buttons_switch_profile_and_the_last_one_wraps_to_the_first(self):
        self.convert()
        first = self.written("Fixture", "Page 01.json")["keys"][0]
        second = self.written("Fixture", "Page 02.json")["keys"][0]
        self.assertEqual((first["action"]["uuid"], first["settings"]), (SWITCH, {"device": SERIAL, "profile": "Fixture/Page 02"}))
        self.assertEqual(second["settings"], {"device": SERIAL, "profile": "Fixture/Page 01"})

    def test_a_hotkey_becomes_input_simulation_with_its_modifiers_held_around_the_key(self):
        self.convert()
        key = self.written("Fixture", "Page 02.json")["keys"][1]
        self.assertEqual(key["action"]["uuid"], INPUT)
        self.assertEqual(key["settings"], {"down": "[Key(Control, Press), Key(Unicode('c'), Click), Key(Control, Release)]"})

    def test_the_title_the_windows_app_saved_is_kept_and_a_missing_alignment_is_top_at_size_11(self):
        self.convert()
        state = self.written("Fixture", "Page 01.json")["keys"][0]["states"][0]
        self.assertEqual((state["text"], state["alignment"], state["size"]), ("Next", "top", 11))

    def test_the_fonts_colours_and_styles_of_a_title_come_across_with_the_windows_family_names_mapped(self):
        styled = {"Title": "Go", "ShowTitle": False, "TitleColor": "#112233", "TitleAlignment": "bottom", "FontFamily": "Arial", "FontStyle": "Bold", "FontSize": 14, "FontUnderline": True, "OutlineThickness": 2}
        self.page(PROFILE, PAGE_ONE, {"0,0": dict(action("com.elgato.streamdeck.page.next"), States=[styled])})
        self.convert()
        state = self.written("Fixture", "Page 01.json")["keys"][0]["states"][0]
        self.assertEqual((state["text"], state["show"], state["colour"], state["alignment"], state["family"], state["style"], state["size"], state["underline"], state["stroke_size"]), ("Go", False, "#112233", "bottom", "Liberation Sans", "Bold", 14, True, 2))

    def test_a_hotkey_with_a_key_enigo_does_not_know_is_dropped_and_reported_with_its_code(self):
        self.page(PROFILE, PAGE_ONE, {"0,0": action("com.elgato.streamdeck.system.hotkey", "Odd", {"Hotkeys": [{"VKeyCode": 67, "KeyModifiers": 0}, {"VKeyCode": 999, "KeyModifiers": 0}]})})
        self.convert()
        self.assertIsNone(self.written("Fixture", "Page 01.json")["keys"][0])
        self.assertEqual(self.reported()["losses_details"], {"key has no equivalent in enigo": {"999": 1}})

    def test_the_default_profile_decides_what_each_device_opens(self):
        self.profile(SECOND, "Default Profile", [PAGE_ONE])
        self.page(SECOND, PAGE_ONE, {})
        self.convert()
        self.assertEqual(json.loads((self.config / "profiles" / f"{SERIAL}.json").read_text()), {"selected_profile": "Default Profile/Page 01"})

    def test_a_folder_becomes_a_profile_and_its_back_button_returns_to_the_page_that_opened_it(self):
        self.page(PROFILE, PAGE_ONE, {"0,0": action("com.elgato.streamdeck.profile.openchild", "Tools", {"ProfileUUID": FOLDER_PAGE.upper()})})
        self.page(PROFILE, FOLDER_PAGE, {"0,0": action("com.elgato.streamdeck.profile.backtoparent", "Back")})
        self.convert()
        folder = self.written("Fixture", "Folder 01 Tools.json")
        opener = self.written("Fixture", "Page 01.json")["keys"][0]
        self.assertEqual(opener["settings"], {"device": SERIAL, "profile": "Fixture/Folder 01 Tools"})
        self.assertEqual(folder["keys"][0]["settings"], {"device": SERIAL, "profile": "Fixture/Page 01"})

    def test_a_dot_in_a_profile_name_becomes_a_dash(self):
        self.profile(SECOND, "GSX.Menu", [PAGE_ONE])
        self.page(SECOND, PAGE_ONE, {})
        self.convert()
        self.assertTrue((self.config / "profiles" / SERIAL / "GSX-Menu" / "Page 01.json").exists())

    def test_a_model_that_is_not_converted_is_left_out_and_reported(self):
        self.profile(SECOND, "Virtual", [PAGE_ONE], model="20GAV9901")
        self.page(SECOND, PAGE_ONE, {})
        self.convert()
        self.assertFalse((self.config / "profiles" / SERIAL / "Virtual").exists())
        self.assertEqual([entry["elgato"] for entry in self.reported()["excluded"]], ["Virtual"])
        self.assertIn("not converted", self.reported()["excluded"][0]["reason"])

    def test_a_model_with_no_device_given_is_left_out_and_reported(self):
        self.profile(SECOND, "Big deck", [PAGE_ONE], model="20GAT9902")
        self.page(SECOND, PAGE_ONE, {})
        self.convert()
        excluded = self.reported()["excluded"]
        self.assertEqual([(entry["elgato"], entry["reason"]) for entry in excluded], [("Big deck", "no --device given for this model")])

    def test_two_devices_get_their_own_folders_and_sizes(self):
        self.profile(SECOND, "Big deck", [PAGE_ONE], model="20GAT9902")
        self.page(SECOND, PAGE_ONE, {"7,3": action("com.elgato.streamdeck.page.next", "Next")})
        result = self.convert(devices=(DEVICE, "20GAT9902=sd-TEST0002"))
        self.assertEqual(result.returncode, 0, result.stderr)
        big = json.loads((self.config / "profiles" / "sd-TEST0002" / "Big deck" / "Page 01.json").read_text())
        self.assertEqual((len(big["keys"]), len(big["sliders"])), (32, 0))
        self.assertIsNotNone(big["keys"][31])

    def test_skip_leaves_a_named_profile_out(self):
        result = self.convert("--skip", "Fixture")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.reported()["excluded"][0]["reason"], "named in --skip")
        self.assertFalse((self.config / "profiles" / SERIAL / "Fixture").exists())

    def test_a_button_without_an_equivalent_is_counted_with_an_english_reason(self):
        self.page(PROFILE, PAGE_ONE, {"0,0": action("com.elgato.streamdeck.system.sleep", "Sleep")})
        self.convert()
        self.assertEqual(self.reported()["losses_by_reason"], {"Elgato built-in action has no equivalent in OpenDeck": 1})
        self.assertIsNone(self.written("Fixture", "Page 01.json")["keys"][0])

    def test_the_report_and_the_messages_hold_no_portuguese(self):
        self.page(PROFILE, PAGE_ONE, {"0,0": action("com.elgato.streamdeck.system.sleep")})
        result = self.convert()
        self.assertIsNone(re.search(r"[ãõçáéíóúâêô]", result.stdout + result.stderr + self.report.read_text()))

    def test_images_of_a_button_are_copied_under_the_profile(self):
        write(self.source / "ProfilesV3" / f"{PROFILE.upper()}.sdProfile" / "Profiles" / PAGE_ONE.upper() / "Images" / "key.png", "png")
        page = self.source / "ProfilesV3" / f"{PROFILE.upper()}.sdProfile" / "Profiles" / PAGE_ONE.upper() / "manifest.json"
        data = json.loads(page.read_text())
        data["Controllers"][0]["Actions"]["0,0"]["States"][0]["Image"] = "Images/key.png"
        page.write_text(json.dumps(data))
        self.convert()
        self.assertEqual(self.reported()["images_copied"], 1)
        self.assertEqual(len(list((self.config / "images" / SERIAL / "Fixture" / "Page 01").rglob("0.png"))), 1)

    def test_a_plugin_the_windows_app_has_is_copied_unless_opendeck_already_has_it(self):
        self.plugin("com.example.tool", plugin_manifest("com.example.tool.do"))
        write_json(self.config / "plugins" / "com.other.sdPlugin" / "manifest.json", plugin_manifest("com.other.do"))
        self.plugin("com.other", plugin_manifest("com.other.do"))
        self.page(PROFILE, PAGE_ONE, {"0,0": action("com.example.tool.do", "Do"), "1,0": action("com.other.do", "Other")})
        self.convert()
        report = self.reported()["plugins_install"]
        self.assertEqual((report["com.example.tool"], report["com.other"]), ("installed", "already installed; left as it is"))
        self.assertTrue((self.config / "plugins" / "com.example.tool.sdPlugin" / "manifest.json").exists())

    def test_a_marketplace_plugin_with_an_encrypted_manifest_is_reported_and_not_installed(self):
        self.plugin("com.market.thing", None, raw=b"ELGATO" + b"\x00" * 20)
        self.page(PROFILE, PAGE_ONE, {"0,0": action("com.market.thing.act", "Thing")})
        self.convert()
        self.assertEqual(self.reported()["plugins_install"]["com.market.thing"], "encrypted by the Elgato Marketplace; not installed")
        self.assertFalse((self.config / "plugins" / "com.market.thing.sdPlugin").exists())
        self.assertEqual(list(self.reported()["losses_by_reason"]), ["Marketplace plugin with an encrypted manifest, which OpenDeck cannot read"])

    def test_adapted_plugins_are_installed_first(self):
        adapted = self.root / "adapted"
        write_json(adapted / "com.mine.sdPlugin" / "manifest.json", plugin_manifest("com.mine.do"))
        self.convert("--adapted", str(adapted))
        self.assertEqual(self.reported()["plugins_install"]["com.mine"], "installed from --adapted")
        self.assertTrue((self.config / "plugins" / "com.mine.sdPlugin" / "manifest.json").exists())

    def test_system_vitals_buttons_take_the_gpu_and_network_choices_from_the_options(self):
        write_json(self.config / "plugins" / f"{REDLINE}.sdPlugin" / "manifest.json", plugin_manifest(f"{REDLINE}.cpu", f"{REDLINE}.gpu", f"{REDLINE}.net"))
        self.page(PROFILE, PAGE_ONE, {
            "0,0": action("com.vivremotion.systemvitals.plugin", "", {"my_title": "GPU"}),
            "1,0": action("com.vivremotion.systemvitals.plugin", "", {"my_title": "LAN Mbp/s"}),
            "2,0": action("com.vivremotion.systemvitals.plugin", "", {"my_title": "CPU"}),
        })
        self.convert("--gpu-selector", "vendor:1", "--network-interface", "link0")
        keys = self.written("Fixture", "Page 01.json")["keys"]
        self.assertEqual(keys[0]["settings"], {"barMode": "load", "gpuSelector": "vendor:1"})
        self.assertEqual(keys[1]["settings"], {"networkInterface": "link0"})
        self.assertEqual(keys[2]["settings"], {"barMode": "load"})

    def test_system_vitals_buttons_without_the_options_carry_no_machine_choice(self):
        write_json(self.config / "plugins" / f"{REDLINE}.sdPlugin" / "manifest.json", plugin_manifest(f"{REDLINE}.gpu"))
        self.page(PROFILE, PAGE_ONE, {"0,0": action("com.vivremotion.systemvitals.plugin", "", {"my_title": "GPU"})})
        self.convert()
        self.assertEqual(self.written("Fixture", "Page 01.json")["keys"][0]["settings"], {"barMode": "load"})

    def test_it_does_not_touch_the_windows_source(self):
        before = tree(self.source)
        self.convert()
        self.assertEqual(tree(self.source), before)


class ConverterArgumentsTest(ConverterCase):
    def test_no_device_is_refused_before_anything_is_written(self):
        result = self.convert(devices=())
        self.assertEqual(result.returncode, 2)
        self.assertIn("--device", result.stderr)
        self.assertFalse(self.report.exists())
        self.assertFalse((self.config / "profiles").exists())

    def test_a_device_that_does_not_look_like_model_equals_sd_serial_is_refused(self):
        for bad in ("sd-TEST0001", "20GBD9901=TEST0001", "NOPE=sd-TEST0001", "20GBD9901=sd-"):
            with self.subTest(bad):
                result = self.convert(devices=(bad,))
                self.assertEqual(result.returncode, 2)

    def test_the_source_and_the_config_are_required(self):
        result = subprocess.run([str(ADDON / "convert-profiles"), "--device", DEVICE], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


class RebuildCase(unittest.TestCase):
    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("needs git")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addon = self.root / "addon"
        shutil.copytree(ADDON, self.addon, ignore=shutil.ignore_patterns("__pycache__"))
        self.render = self.git_repo("render", {"src/components/pixmap.rs": "draw checkerboard\n"})
        self.render_rev = self.git(self.render, "rev-parse", "--short=8", "HEAD").strip()
        manifest = f'[dependencies]\nstreamdeck-strip-render = {{ git = "https://github.com/FrostyCoolSlug/streamdeck-strip-render", rev = "{self.render_rev}" }}\n'
        self.opendeck = self.git_repo("opendeck", {".gitignore": "node_modules\ntarget\n", "src-tauri/Cargo.toml": manifest, "src-tauri/src/elgato.rs": "ignore swipe\n", "src-tauri/src/encoder_layouts.rs": "title as text\n", "package.json": "{}\n"})
        self.git(self.opendeck, "tag", "v2.14.2")
        self.patch(self.render, "src/components/pixmap.rs", "draw nothing\n", "strip-render-no-checkerboard.patch")
        self.patch(self.opendeck, "src-tauri/src/elgato.rs", "change page on swipe\n", "swipe-changes-the-page.patch")
        self.patch(self.opendeck, "src-tauri/src/encoder_layouts.rs", "title like Elgato\n", "dial-title-like-elgato.patch")
        self.cache = self.root / "cache"
        self.bin = self.root / "bin"
        self.calls = self.root / "calls"
        stub(self.bin / "npm", f'#!/bin/sh\nprintf "%s\\n" "npm $*" >> "{self.calls}"\n[ "$1" != install ] || mkdir -p node_modules\n')
        stub(self.bin / "cargo", f'#!/bin/sh\nprintf "%s\\n" "cargo $* | deno on PATH: $(command -v deno)" >> "{self.calls}"\n[ "$1" != build ] || {{ mkdir -p target/release; printf "patched opendeck binary" > target/release/opendeck; }}\n')

    def tearDown(self):
        self.temp.cleanup()

    def git(self, folder, *args):
        return subprocess.run(["git", "-C", str(folder), "-c", "user.email=t@t", "-c", "user.name=t", *args], capture_output=True, text=True, check=True).stdout

    def git_repo(self, name, files):
        folder = self.root / name
        for relative, text in files.items():
            write(folder / relative, text)
        subprocess.run(["git", "init", "-q", str(folder)], check=True)
        self.git(folder, "add", "-A")
        self.git(folder, "commit", "-qm", "base")
        return folder

    def patch(self, repo, relative, new, name):
        old = (repo / relative).read_text()
        (repo / relative).write_text(new)
        diff = self.git(repo, "diff")
        (repo / relative).write_text(old)
        write(self.addon / "patches" / name, diff)

    def environment(self, **extra):
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "FSOL_CACHE": str(self.cache),
            "OPENDECK_REPOSITORY": str(self.opendeck),
            "STRIP_RENDER_REPOSITORY": str(self.render),
            "OPENDECK_FETCH_ATTEMPTS": "1",
            "OPENDECK_FETCH_PAUSE": "0",
        }
        env.update(extra)
        return env

    def rebuild(self, *args, **extra):
        return subprocess.run([str(self.addon / "rebuild"), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)


class RebuildTest(RebuildCase):
    def test_it_checks_out_the_tag_applies_the_patches_and_builds_the_binary(self):
        result = self.rebuild("2.14.2")
        self.assertEqual(result.returncode, 0, result.stderr)
        source = self.cache / "opendeck" / "source"
        self.assertEqual((source / "src-tauri/src/elgato.rs").read_text(), "change page on swipe\n")
        self.assertEqual((source / "src-tauri/src/encoder_layouts.rs").read_text(), "title like Elgato\n")
        binary = source / "src-tauri" / "target" / "release" / "opendeck"
        self.assertIn(str(binary), result.stdout)
        self.assertIn(f"sudo install -m 755 '{binary}' /usr/bin/opendeck", result.stdout)

    def test_the_strip_render_checkout_is_patched_and_cargo_is_pointed_at_it(self):
        self.rebuild("2.14.2")
        render = self.cache / "opendeck" / "strip-render"
        self.assertEqual((render / "src/components/pixmap.rs").read_text(), "draw nothing\n")
        manifest = (self.cache / "opendeck" / "source" / "src-tauri" / "Cargo.toml").read_text()
        self.assertIn('[patch."https://github.com/FrostyCoolSlug/streamdeck-strip-render"]', manifest)
        self.assertIn(f'streamdeck-strip-render = {{ path = "{render}" }}', manifest)

    def test_the_frontend_is_installed_once_and_built_then_tested_then_the_binary_is_built(self):
        self.rebuild("2.14.2")
        calls = [line.split(" | ")[0] for line in self.calls.read_text().splitlines()]
        self.assertEqual(calls, ["npm install --no-audit --no-fund", "npm run build", "cargo test --release --features custom-protocol encoder_layouts", "cargo build --release --features custom-protocol"])

    def test_cargo_sees_a_stand_in_deno_that_does_nothing(self):
        self.rebuild("2.14.2")
        line = [line for line in self.calls.read_text().splitlines() if line.startswith("cargo build")][0]
        deno = line.split("deno on PATH: ")[1]
        self.assertEqual(deno, str(self.cache / "opendeck" / "bin" / "deno"))
        self.assertEqual(subprocess.run([deno]).returncode, 0)

    def test_the_second_run_does_not_install_the_frontend_again_and_starts_from_a_clean_tree(self):
        self.rebuild("2.14.2")
        self.calls.unlink()
        result = self.rebuild("2.14.2")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [line.split(" | ")[0] for line in self.calls.read_text().splitlines()]
        self.assertNotIn("npm install --no-audit --no-fund", calls)
        manifest = (self.cache / "opendeck" / "source" / "src-tauri" / "Cargo.toml").read_text()
        self.assertEqual(manifest.count("[patch."), 1)

    def test_it_records_the_binary_and_the_three_patches_for_the_check(self):
        self.rebuild("2.14.2")
        record = (self.cache / "opendeck" / "patched-build.txt").read_text().splitlines()
        self.assertEqual(record[0], "version 2.14.2")
        self.assertEqual(record[1], f"binary {sha256(b'patched opendeck binary')}")
        names = [line.split()[2] for line in record[2:]]
        self.assertEqual(sorted(names), ["dial-title-like-elgato.patch", "strip-render-no-checkerboard.patch", "swipe-changes-the-page.patch"])
        for line in record[2:]:
            _, sha, name = line.split()
            self.assertEqual(sha, sha256((self.addon / "patches" / name).read_bytes()))

    def test_without_fsol_cache_it_works_in_the_same_cache_folder_the_add_on_scripts_use(self):
        env = self.environment(XDG_CACHE_HOME=str(self.root / "xdg"))
        del env["FSOL_CACHE"]
        result = subprocess.run([str(self.addon / "rebuild"), "2.14.2"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "xdg" / "flightsim-on-linux" / "opendeck" / "patched-build.txt").exists())

    def test_the_version_comes_from_pacman_when_none_is_given(self):
        stub(self.bin / "pacman", '#!/bin/sh\necho "opendeck 2.14.2-1.1"\n')
        result = self.rebuild()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OpenDeck 2.14.2", result.stdout)

    def test_no_version_and_no_pacman_asks_for_one(self):
        stub(self.bin / "pacman", "#!/bin/sh\nexit 1\n")
        result = self.rebuild()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: rebuild", result.stderr)

    def test_a_version_without_a_tag_stops_before_building(self):
        result = self.rebuild("9.9.9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no tag v9.9.9", result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.cache / "opendeck" / "patched-build.txt").exists())

    def test_a_patch_that_does_not_fit_the_new_version_stops_and_records_nothing(self):
        write(self.opendeck / "src-tauri/src/elgato.rs", "a different file\n")
        self.git(self.opendeck, "commit", "-aqm", "change")
        self.git(self.opendeck, "tag", "-f", "v2.14.2")
        result = self.rebuild("2.14.2")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.cache / "opendeck" / "patched-build.txt").exists())

    def test_an_opendeck_that_takes_strip_render_another_way_stops_with_a_reason(self):
        write(self.opendeck / "src-tauri/Cargo.toml", '[dependencies]\nstreamdeck-strip-render = "0.2"\n')
        self.git(self.opendeck, "commit", "-aqm", "crates.io")
        self.git(self.opendeck, "tag", "-f", "v2.14.2")
        result = self.rebuild("2.14.2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("pinned git revision", result.stderr)

    def test_a_missing_tool_is_named(self):
        (self.bin / "cargo").unlink()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            for tool in ("bash", "dirname", "readlink", "git", "sed", "mkdir", "env", "cut", "sha256sum", "grep", "head", "timeout", "sleep", "chmod", "printf"):
                found = shutil.which(tool)
                if found:
                    os.symlink(found, path / tool)
            os.symlink(self.bin / "npm", path / "npm")
            result = subprocess.run([str(self.addon / "rebuild"), "2.14.2"], env=self.environment(PATH=str(path)), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cargo is missing", result.stderr)


class AaoPluginTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plugin = self.root / "config" / "opendeck" / "plugins" / "com.lorbysi.aao.sdPlugin"
        self.settings = self.plugin / "settings.js"
        write(self.settings, 'const AAO_URL = "http://localhost:43380/webapi";\nconst OTHER = 1;\n')

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, *args):
        return subprocess.run([str(ADDON / "aao-plugin"), *args], env={"PATH": os.environ["PATH"], "HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / "config")}, capture_output=True, text=True)

    def test_it_moves_the_port_and_keeps_the_original_beside_the_file(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.settings.read_text(), 'const AAO_URL = "http://localhost:43390/webapi";\nconst OTHER = 1;\n')
        self.assertEqual(Path(f"{self.settings}.orig").read_text(), 'const AAO_URL = "http://localhost:43380/webapi";\nconst OTHER = 1;\n')

    def test_it_takes_the_port_it_is_given_and_never_replaces_the_first_backup(self):
        self.run_script("5000")
        self.run_script("6000")
        self.assertIn("localhost:6000/webapi", self.settings.read_text())
        self.assertIn("localhost:43380/webapi", Path(f"{self.settings}.orig").read_text())

    def test_restore_gives_the_plugin_its_own_file_back(self):
        self.run_script()
        result = self.run_script("--restore")
        self.assertEqual(result.returncode, 0)
        self.assertIn("43380", self.settings.read_text())
        self.assertFalse(Path(f"{self.settings}.orig").exists())

    def test_restore_without_a_copy_refuses(self):
        self.assertEqual(self.run_script("--restore").returncode, 1)

    def test_a_port_that_is_not_one_is_refused_and_nothing_changes(self):
        before = tree(self.root)
        for bad in ("abc", "0", "70000", "-1", "4 3"):
            with self.subTest(bad):
                self.assertEqual(self.run_script(bad).returncode, 2)
        self.assertEqual(tree(self.root), before)

    def test_a_settings_file_without_the_line_is_left_alone(self):
        self.settings.write_text("const SOMETHING = 1;\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.settings.read_text(), "const SOMETHING = 1;\n")

    def test_no_plugin_says_where_it_looked(self):
        shutil.rmtree(self.plugin)
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("com.lorbysi.aao.sdPlugin", result.stderr)


class CheckCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.binary = stub(self.bin / "opendeck", "#!/bin/sh\n# stock\n")
        self.cache = self.root / "cache"
        self.pacman_report = ""
        self.pacman()

    def tearDown(self):
        self.temp.cleanup()

    def pacman(self, report=""):
        stub(self.bin / "pacman", f'#!/bin/sh\ncat <<\'EOF\'\n{report}EOF\n[ -z "{report.strip()}" ] || exit 1\n')

    def record(self, binary_sha):
        write(self.cache / "opendeck" / "patched-build.txt", f"version 2.14.2\nbinary {binary_sha}\n")

    def aao(self, port):
        write(self.root / "config" / "opendeck" / "plugins" / "com.lorbysi.aao.sdPlugin" / "settings.js", f'const AAO_URL = "http://localhost:{port}/webapi";\n')

    def check(self, path=None, **extra):
        env = {"PATH": path or f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / "config"), "FSOL_CACHE": str(self.cache)}
        env.update(extra)
        return subprocess.run([str(ADDON / "check")], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def words(self, result):
        return [line.split()[0] for line in result.stdout.splitlines()]


class CheckTest(CheckCase):
    def test_a_stock_package_binary_passes_and_is_called_stock(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.words(result), ["ok", "ok"])
        self.assertIn("is the stock one", result.stdout)
        self.assertIn("optional", result.stdout)

    def test_a_pacman_that_speaks_another_language_is_understood_by_the_path_alone(self):
        self.pacman(f"atenção: opendeck: {self.binary} (soma de verificação SHA256 incorreta)\n")
        result = self.check()
        self.assertIn("differs from the package", result.stdout)

    def test_a_binary_that_pacman_reports_as_changed_is_not_called_stock(self):
        self.pacman(f"warning: opendeck: {self.binary} (SHA256 checksum mismatch)\n")
        result = self.check()
        self.assertEqual(result.returncode, 0)
        self.assertIn("differs from the package, and rebuild did not make it", result.stdout)
        self.assertNotIn("stock one", result.stdout)

    def test_pacman_reporting_another_file_of_the_package_does_not_make_the_binary_changed(self):
        self.pacman("warning: opendeck: /usr/share/applications/opendeck.desktop (SHA256 checksum mismatch)\n")
        self.assertIn("is the stock one", self.check().stdout)

    def test_the_binary_rebuild_made_is_called_the_patched_build(self):
        self.record(sha256(self.binary.read_bytes()))
        self.pacman(f"warning: opendeck: {self.binary} (SHA256 checksum mismatch)\n")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("is the patched build that rebuild made", result.stdout)

    def test_the_record_is_read_from_the_default_cache_folder_when_fsol_cache_is_unset(self):
        write(self.root / "xdg" / "flightsim-on-linux" / "opendeck" / "patched-build.txt", f"version 2.14.2\nbinary {sha256(self.binary.read_bytes())}\n")
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_CACHE_HOME": str(self.root / "xdg")}
        result = subprocess.run([str(ADDON / "check")], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertIn("is the patched build that rebuild made", result.stdout)

    def test_a_package_update_that_put_the_stock_binary_back_fails_with_the_way_out(self):
        self.record(sha256(b"an earlier patched binary"))
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.words(result), ["ok", "FAIL"])
        self.assertIn("Run rebuild again", result.stdout)

    def test_no_pacman_and_no_record_still_says_stock_without_claiming_more(self):
        (self.bin / "pacman").unlink()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            for tool in ("bash", "dirname", "readlink", "sed", "head", "cut", "sha256sum", "grep", "env"):
                os.symlink(shutil.which(tool), path / tool)
            os.symlink(self.binary, path / "opendeck")
            result = self.check(str(path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("is the stock one", result.stdout)

    def test_no_opendeck_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            for tool in ("bash", "dirname", "readlink", "sed", "head", "cut", "sha256sum", "grep", "env"):
                os.symlink(shutil.which(tool), path / tool)
            result = self.check(str(path))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.words(result), ["FAIL"])

    def test_the_aao_plugin_on_its_own_port_fails(self):
        self.aao(43380)
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.words(result)[-1], "FAIL")
        self.assertIn("addon launch opendeck aao-plugin", result.stdout)

    def test_the_aao_plugin_on_another_port_passes(self):
        self.aao(43390)
        result = self.check()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.words(result), ["ok", "ok", "ok"])

    def test_without_the_aao_plugin_there_is_no_line_about_it(self):
        self.assertEqual(len(self.check().stdout.splitlines()), 2)

    def test_check_changes_nothing(self):
        self.aao(43390)
        self.record(sha256(self.binary.read_bytes()))
        before = tree(self.root)
        self.check()
        self.assertEqual(tree(self.root), before)


class ApplyAndUndoTest(unittest.TestCase):
    def test_undo_gives_the_plugin_its_own_settings_back(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin = Path(temp) / "opendeck/plugins/com.lorbysi.aao.sdPlugin"
            write(plugin / "settings.js", "edited")
            write(plugin / "settings.js.orig", "original")
            result = subprocess.run([str(ADDON / "undo")], capture_output=True, text=True, env={**os.environ, "XDG_CONFIG_HOME": temp})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((plugin / "settings.js").read_text(), "original")
            self.assertFalse((plugin / "settings.js.orig").exists())

    def test_apply_and_undo_say_there_is_nothing_to_do_and_change_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root / "file", "x")
            before = tree(root)
            for name in ("apply", "undo"):
                result = subprocess.run([str(ADDON / name)], capture_output=True, text=True, cwd=root)
                self.assertEqual(result.returncode, 0)
                self.assertIn("already nothing to", result.stdout)
            self.assertEqual(tree(root), before)


class PatchFilesTest(unittest.TestCase):
    def test_the_swipe_patch_pages_through_the_names_the_converter_writes(self):
        self.assertIn('format!("{group}/Page ")', (ADDON / "patches" / "swipe-changes-the-page.patch").read_text())
        self.assertIn('f"{candidate}/Page {index:02d}"', (ADDON / "parts" / "convert.py").read_text())

    def test_the_strip_render_patch_has_no_path_of_the_machine_that_made_it(self):
        text = (ADDON / "patches" / "strip-render-no-checkerboard.patch").read_text()
        self.assertNotIn("/home/", text)
        self.assertTrue(text.startswith("diff --git a/src/components/pixmap.rs b/src/components/pixmap.rs\n"))

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_the_patch_files_are_well_formed_diffs(self):
        with tempfile.TemporaryDirectory() as temp:
            for patch in (ADDON / "patches").glob("*.patch"):
                result = subprocess.run(["git", "apply", "--stat", str(patch)], capture_output=True, text=True, cwd=temp)
                self.assertEqual(result.returncode, 0, f"{patch.name}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
