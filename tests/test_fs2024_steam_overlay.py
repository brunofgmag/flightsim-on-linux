import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("fs2024_steam_overlay", ROOT / "proton" / "fs2024_steam_overlay.py")
overlay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overlay)

LAYERS = "/proton/files/share/steam-overlay-wayland/implicit_layer.d"
CONTAINER_LAYERS = "/usr/lib/pressure-vessel/overrides/share/vulkan/implicit_layer.d"
INJECTED = "/tmp/pressure-vessel-libs-GxLxSL/${LIB}/gameoverlayrenderer.so"


class EnableTest(unittest.TestCase):
    def test_inside_the_container_the_layer_joins_the_search_path_pressure_vessel_set(self):
        env = {"LD_PRELOAD": INJECTED, "VK_IMPLICIT_LAYER_PATH": CONTAINER_LAYERS}
        self.assertTrue(overlay.enable(env, LAYERS))
        self.assertEqual(env["VK_IMPLICIT_LAYER_PATH"], f"{CONTAINER_LAYERS}:{LAYERS}")
        self.assertEqual(env["WINE_WAYLAND_STEAM_OVERLAY_LAYER"], "1")
        self.assertNotIn("VK_ADD_IMPLICIT_LAYER_PATH", env)

    def test_outside_a_container_the_default_layer_folders_stay_searched(self):
        env = {"LD_PRELOAD": INJECTED}
        self.assertTrue(overlay.enable(env, LAYERS))
        self.assertEqual(env["VK_ADD_IMPLICIT_LAYER_PATH"], LAYERS)
        self.assertNotIn("VK_IMPLICIT_LAYER_PATH", env)

    def test_without_steams_overlay_nothing_changes(self):
        env = {"LD_PRELOAD": "/usr/lib/libgamemodeauto.so.0", "VK_IMPLICIT_LAYER_PATH": CONTAINER_LAYERS}
        self.assertFalse(overlay.enable(env, LAYERS))
        self.assertEqual(env, {"LD_PRELOAD": "/usr/lib/libgamemodeauto.so.0", "VK_IMPLICIT_LAYER_PATH": CONTAINER_LAYERS})

    def test_the_off_switch_wins_over_an_injected_overlay(self):
        env = {"LD_PRELOAD": INJECTED, "DISABLE_WINE_WAYLAND_STEAM_OVERLAY_LAYER": "1", "VK_IMPLICIT_LAYER_PATH": CONTAINER_LAYERS}
        self.assertFalse(overlay.enable(env, LAYERS))
        self.assertNotIn("WINE_WAYLAND_STEAM_OVERLAY_LAYER", env)
        self.assertEqual(env["VK_IMPLICIT_LAYER_PATH"], CONTAINER_LAYERS)

    def test_an_off_switch_set_to_zero_leaves_the_bridge_on(self):
        env = {"LD_PRELOAD": INJECTED, "DISABLE_WINE_WAYLAND_STEAM_OVERLAY_LAYER": "0"}
        self.assertTrue(overlay.enable(env, LAYERS))

    def test_a_second_call_does_not_list_the_folder_twice(self):
        env = {"LD_PRELOAD": INJECTED, "VK_IMPLICIT_LAYER_PATH": CONTAINER_LAYERS}
        overlay.enable(env, LAYERS)
        overlay.enable(env, LAYERS)
        self.assertEqual(env["VK_IMPLICIT_LAYER_PATH"], f"{CONTAINER_LAYERS}:{LAYERS}")


class ProtonPatchTest(unittest.TestCase):
    def test_the_proton_patch_calls_enable_with_the_folder_the_build_installs_into(self):
        patch = (ROOT / "patches" / "proton" / "steam-overlay-wayland.patch").read_text()
        build = (ROOT / "proton" / "build-proton-fs2024.sh").read_text()
        self.assertIn('+            fs2024_steam_overlay.enable(self.env, g_proton.path("files/share/steam-overlay-wayland/implicit_layer.d"))', patch)
        self.assertIn('OVERLAY_LAYER_MANIFEST_DIR="share/steam-overlay-wayland/implicit_layer.d"', build)


if __name__ == "__main__":
    unittest.main()
