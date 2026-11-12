import re
import unittest
from pathlib import Path

from tests.test_maddog_addon import page_problems
from tools import build_site

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "site" / "pages"
PAGE = PAGES / "launch-options.html"
SECTIONS = ["Where they go", "Needed by everyone", "When you see the problem", "By machine", "Preferences", "One line for each profile"]
LEFT_OUT = ("VKD3D_FRAME_RATE", "QT_WAYLAND_DISABLE_WINDOWDECORATION", "Fastlaunch")
PRESET_M = "DXVK_NVAPI_DRS_SETTINGS=ngx_dlss_sr_override=on,ngx_dlss_sr_override_render_preset_selection=render_preset_m"


def page_text():
    return PAGE.read_text()


def section(text, heading):
    after = text.split(f"<h2>{heading}</h2>")[1]
    return re.split(r"<h2>", after)[0]


def subsection(text, heading):
    after = re.split(rf"<h3>(?:<code>)?{re.escape(heading)}(?:</code>)?</h3>", text)[1]
    return re.split(r"<h3>|<h2>", after)[0]


def profile_lines(text):
    block = section(text, "One line for each profile")
    return {heading: line for heading, line in re.findall(r"<h3>(.*?)</h3>\s*<pre><code>(.*?)</code></pre>", block, re.S)}


class LaunchOptionsPageTest(unittest.TestCase):
    def setUp(self):
        self.text = page_text()

    def test_it_follows_the_template_and_has_its_sections_in_order(self):
        self.assertEqual(page_problems("launch-options", sections=SECTIONS), [])
        self.assertEqual(re.findall(r"<h2>(.*?)</h2>", self.text), SECTIONS)

    def test_it_is_in_the_navigation_the_home_page_the_add_on_list_and_the_llms_file(self):
        self.assertIn("launch-options.html", dict(build_site.NAV))
        self.assertIn('href="launch-options.html"', (PAGES / "index.html").read_text())
        self.assertIn('href="launch-options.html"', (PAGES / "addons.html").read_text())
        self.assertIn("launch-options.html", (ROOT / "site" / "llms.txt").read_text())

    def test_it_is_in_the_committed_docs_with_the_navigation(self):
        built = (ROOT / "docs" / "launch-options.html").read_text()
        self.assertIn('<a href="launch-options.html" aria-current="page">Launch options</a>', built)
        self.assertIn('<a href="launch-options.html">Launch options</a>', (ROOT / "docs" / "index.html").read_text())

    def test_nothing_is_needed_by_everyone(self):
        text = section(self.text, "Needed by everyone")
        self.assertIn("Nothing", text)
        self.assertNotIn("<code>", text.replace("<code>start.html</code>", ""))

    def test_the_problem_options_each_say_when_to_use_them(self):
        text = section(self.text, "When you see the problem")
        for option, needed in (
            ("PROTON_NO_NTSYNC=1", "freezes in the main menu or while loading an aircraft"),
            ("WAYLANDDRV_FOCUS_LOSS=0", "keeps the simulator from losing focus in a multi-monitor setup"),
            ("STEAM_COMPAT_LIBRARY_PATHS", "Only the ChasePlane bridge needs it"),
        ):
            self.assertIn(option, text)
            self.assertIn(needed if option != "PROTON_NO_NTSYNC=1" else "froze in the main menu or while loading an aircraft", text)
        self.assertIn("PROTON_USE_NTSYNC=0", text)
        self.assertIn("does nothing under the X11 driver", text)

    def test_the_machine_sections_name_each_kind_of_machine(self):
        text = section(self.text, "By machine")
        for heading in ("An NVIDIA GPU", "An HDR monitor", "A Wayland session", "Two GPUs, or an X3D CPU with two chiplets", "MFG Unlocker with XMX"):
            self.assertIn(f"<h3>{heading}</h3>", text)

    def test_hdr_is_for_an_hdr_monitor_and_needs_the_wayland_driver(self):
        text = subsection(section(self.text, "By machine"), "An HDR monitor")
        for needed in ("PROTON_ENABLE_HDR=1", "only with an HDR monitor", "Wayland driver", "XWayland", "580.178.04", "615.78.08"):
            self.assertIn(needed, text)

    def test_the_wayland_driver_names_its_price_in_the_tray(self):
        text = subsection(section(self.text, "By machine"), "A Wayland session")
        for needed in ("PROTON_ENABLE_WAYLAND=1", "tray icons", "explorer.exe", "KDE tray", "HDR needs the Wayland driver"):
            self.assertIn(needed, text)

    def test_the_game_script_is_an_example_and_not_a_requirement(self):
        text = subsection(section(self.text, "By machine"), "Two GPUs, or an X3D CPU with two chiplets")
        for needed in ("not a requirement", "systemd-run", "games.slice", "AllowedCPUs=0-7,16-23", "__NV_PRIME_RENDER_OFFLOAD=1", "__VK_LAYER_NV_optimus=NVIDIA_only", "__GLX_VENDOR_LIBRARY_NAME=nvidia", "still apply", "the script exports the same ones", "app-steam-", "Slice=games.slice", "Cpus_allowed_list"):
            self.assertIn(needed, text)
        self.assertIn("exec env -u LD_LIBRARY_PATH -u LD_PRELOAD", text)

    def test_the_xmx_options_carry_their_sources_and_say_what_is_optional(self):
        text = subsection(section(self.text, "By machine"), "MFG Unlocker with XMX")
        for needed in ("620.70.06", "13.7 to 14.4 GB", "3 frames by default", "8.3 ms at 4x on a 120 Hz screen", "11 November 2026", "can make the GPU clock drop", "the output left 120", "only if the picture tears", "you don't need <code>VKD3D_DISABLE_EXTENSIONS=VK_NV_low_latency2</code>", "mfg-unlocker.html"):
            self.assertIn(needed, text)
        self.assertEqual(text.count("optional"), 2)

    def test_preset_m_is_a_preference_recommended_on_nvidia_and_has_no_frame_generation_pair_in_it(self):
        text = section(self.text, "Preferences")
        block = subsection(text, "DLSS Super Resolution preset M, recommended on NVIDIA")
        self.assertIn(PRESET_M, block)
        for needed in ("recommends it on NVIDIA GPUs", "render_preset_m", "plain <code>m</code>", "Render Preset: M", "310.6.0.0"):
            self.assertIn(needed, block)
        shown = re.search(r"<pre><code>(.*?)</code></pre>", block, re.S).group(1)
        self.assertNotIn("fg_override", shown)

    def test_the_frame_generation_preset_is_named_only_to_say_it_is_not_recommended(self):
        mentions = [item for item in re.findall(r"<li>(.*?)</li>", self.text, re.S) if "ngx_dlss_fg_override" in item]
        self.assertEqual(len(mentions), 1)
        self.assertIn("doesn't recommend it", mentions[0])
        self.assertIn("12 November 2026", mentions[0])

    def test_the_focus_loss_option_is_for_other_monitors_and_does_not_promise_alt_tab(self):
        text = subsection(section(self.text, "When you see the problem"), "WAYLANDDRV_FOCUS_LOSS=0")
        for needed in ("multi-monitor setup", "another window on another monitor", "Alt+Tab still makes the simulator window lose focus with the option set"):
            self.assertIn(needed, text)
        self.assertNotIn("foreground window when another window takes the focus", text)

    def test_vkd3d_config_and_the_rebar_preference_are_gone_from_the_page_and_every_profile(self):
        for gone in ("VKD3D_CONFIG", "no_upload_hvv", "no_gpu_upload_heap", "ReBAR"):
            self.assertNotIn(gone, self.text)
        for heading, line in profile_lines(self.text).items():
            self.assertNotIn("VKD3D_CONFIG", line, heading)
        for other in (*PAGES.glob("*.html"), ROOT / "site" / "llms.txt"):
            self.assertNotIn("VKD3D_CONFIG", other.read_text(), other.name)

    def test_options_with_no_documented_reason_are_left_out(self):
        for name in LEFT_OUT:
            self.assertNotIn(name, self.text)

    def test_it_names_nobody_and_nothing_personal(self):
        for name in ("Bruno", "brunom", "/home/", "Jogos"):
            self.assertNotIn(name, self.text)

    def test_every_profile_is_one_line_that_ends_with_the_command_and_the_five_profiles_exist(self):
        lines = profile_lines(self.text)
        self.assertEqual(len(lines), 5, list(lines))
        for heading, line in lines.items():
            self.assertNotIn("\n", line, heading)
            self.assertTrue(line.endswith("%command%"), heading)

    def test_every_option_of_every_profile_is_explained_before_the_profiles(self):
        explained = self.text.split("<h2>One line for each profile</h2>")[0]
        for heading, line in profile_lines(self.text).items():
            for name in re.findall(r"(?:^| )([A-Za-z_][A-Za-z0-9_]*)=", line):
                self.assertIn(name, explained, f"{heading}: {name}")

    def test_no_profile_has_the_frame_generation_preset_or_an_option_left_out(self):
        for heading, line in profile_lines(self.text).items():
            for gone in ("fg_override", "render_preset_c", *LEFT_OUT, "VKD3D_DISABLE_EXTENSIONS"):
                self.assertNotIn(gone, line, heading)

    def test_hdr_and_the_wayland_driver_are_only_in_the_profiles_that_ask_for_them(self):
        lines = profile_lines(self.text)
        self.assertEqual({heading for heading, line in lines.items() if "PROTON_ENABLE_HDR=1" in line}, {"An NVIDIA GPU, Wayland session, HDR monitor", "The same, with the <code>game</code> script for two GPUs or an X3D CPU", "All of it together, for a machine like the test machine"})
        self.assertNotIn("PROTON_ENABLE_WAYLAND", lines["An NVIDIA GPU, X11 driver"])
        self.assertNotIn("PROTON_ENABLE_HDR", lines["MFG Unlocker with XMX"])

    def test_the_game_script_is_only_in_the_profiles_for_two_gpus_and_the_combined_one(self):
        lines = profile_lines(self.text)
        with_script = {heading for heading, line in lines.items() if re.search(r"(?:^| )game %command%$", line)}
        self.assertEqual(with_script, {"The same, with the <code>game</code> script for two GPUs or an X3D CPU", "All of it together, for a machine like the test machine"})

    def test_the_xmx_profile_has_the_three_checked_options_and_the_two_optional_ones(self):
        line = profile_lines(self.text)["MFG Unlocker with XMX"]
        for needed in ('WINEDLLOVERRIDES="version=n,b"', "PROTON_NVIDIA_NVCUDA=1", "PROTON_DLSS_UPGRADE=1", "__GL_0xcfcfa1=0", "VKD3D_SWAPCHAIN_LATENCY_FRAMES=2"):
            self.assertIn(needed, line)
        self.assertNotIn("MAILBOX", line)

    def test_the_xmx_profile_matches_what_the_mfg_unlocker_page_asks_for(self):
        mfg = (PAGES / "mfg-unlocker.html").read_text()
        asked = re.search(r"<pre><code>(WINEDLLOVERRIDES=.*?) %command%</code></pre>", mfg).group(1)
        line = profile_lines(self.text)["MFG Unlocker with XMX"]
        for option in re.findall(r'(?:^| )(\w+=(?:"[^"]*"|\S+))', asked):
            self.assertIn(option, line)


if __name__ == "__main__":
    unittest.main()
