import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "simulator-gpu-memory-warning"

spec = importlib.util.spec_from_file_location("gpu_memory_warning_parts", ADDON / "parts" / "warning.py")
warning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(warning)

PAGE = (
    "<!DOCTYPE html>\r\n<html>\r\n\t<head>\r\n"
    '\t\t<script type="module" src="/Global/notifications.bundle.js"></script>\r\n'
    "\t</head>\r\n</html>\r\n"
)
ENGLISH = "Resource usage exceeds GPU memory capacity, lower graphics settings for better performance"
PORTUGUESE = "O uso de recursos excede a capacidade de memória da GPU. Reduza as configurações gráficas para melhor desempenho"
LAYOUT = """{
  "content": [
    {
      "path": "html_ui/global/notificationspanel.html",
      "size": 11,
      "date": 1
    },
    {
      "path": "html_ui/global/notifications.html",
      "size": %d,
      "date": 134369007820000000
    }
  ]
}"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(text)


def read(path):
    with open(path, encoding="utf-8", newline="") as file:
        return file.read()


def fake_sim(root, page=PAGE):
    write(root / "Packages" / "fs-base-ui" / "html_ui" / "Global" / "notifications.html", page)
    write(root / "Packages" / "fs-base-ui" / "layout.json", LAYOUT % len(page.encode()))
    for language, text in (("en-US", ENGLISH), ("pt-BR", PORTUGUESE)):
        strings = {"LocalisationPackage": {"Language": language, "Strings": {warning.KEY: text, "OTHER.KEY": "other"}}}
        write(root / "Packages" / "fs-base" / f"{language}.locPak", json.dumps(strings, ensure_ascii=False))
    write(root / "Packages" / "fs-base" / "fr-FR.locPak", json.dumps({"LocalisationPackage": {"Strings": {}}}))
    return root


def places(sim, community, backup):
    return warning.Places(sim, community, backup)


def run(action, sim, community, backup):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        status = warning.main([action, str(sim), str(community), str(backup)])
    return status, out.getvalue()


SCRIPT = "const a = 1;\nconst b = 2;\n"


class PageCase(unittest.TestCase):
    def test_the_filter_sits_inside_the_page_right_after_the_bundle_with_the_page_line_endings(self):
        page = warning.filtered_page(PAGE, SCRIPT)
        block = '\t\t<script type="module">\r\nconst a = 1;\r\nconst b = 2;\r\n\t\t</script>\r\n'
        self.assertIn(warning.BUNDLE_TAG + "\r\n" + block, page)
        self.assertEqual(page.replace(block, "", 1), PAGE)
        self.assertNotIn("src=\"/Global/flightsim", page)

    def test_a_page_that_no_longer_loads_the_bundle_is_refused(self):
        with self.assertRaises(warning.WarningError):
            warning.filtered_page(PAGE.replace("notifications.bundle.js", "other.js"), SCRIPT)


class TextsCase(unittest.TestCase):
    def test_every_language_file_that_has_the_warning_gives_its_text(self):
        with tempfile.TemporaryDirectory() as folder:
            sim = fake_sim(Path(folder) / "sim")
            self.assertEqual(warning.warning_texts(places(sim, Path(folder), Path(folder))), sorted([ENGLISH, PORTUGUESE]))

    def test_no_language_file_with_the_warning_is_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            sim = fake_sim(Path(folder) / "sim")
            for pack in (sim / "Packages" / "fs-base").glob("*.locPak"):
                pack.unlink()
            with self.assertRaises(warning.WarningError):
                warning.warning_texts(places(sim, Path(folder), Path(folder)))


class CycleCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.sim = fake_sim(root / "sim")
        self.community = root / "Community"
        self.community.mkdir()
        self.backup = root / "backup"
        self.page = self.sim / "Packages" / "fs-base-ui" / warning.PAGE
        self.layout = self.sim / "Packages" / "fs-base-ui" / "layout.json"
        self.legacy = self.community / warning.PACKAGE
        self.script = warning.filter_script(places(self.sim, self.community, self.backup))

    def tearDown(self):
        self.folder.cleanup()

    def run_action(self, action):
        return run(action, self.sim, self.community, self.backup)

    def test_check_fails_before_apply_passes_after_and_undo_puts_the_original_back(self):
        layout_before = read(self.layout)
        self.assertEqual(self.run_action("check")[0], 1)
        self.assertEqual(self.run_action("apply")[0], 0)
        status, out = self.run_action("check")
        self.assertEqual(status, 0, out)
        self.assertEqual(sum(line.startswith("ok ") for line in out.splitlines()), 2)
        self.assertEqual(read(self.page), warning.filtered_page(PAGE, self.script))
        self.assertEqual(self.run_action("undo")[0], 0)
        self.assertEqual(read(self.page), PAGE)
        self.assertEqual(read(self.layout), layout_before)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.run_action("check")[0], 1)

    def test_apply_gives_the_layout_the_new_page_size_and_touches_no_other_entry(self):
        before = read(self.layout)
        self.run_action("apply")
        after = read(self.layout)
        size = len(warning.filtered_page(PAGE, self.script).encode())
        self.assertEqual(after, before.replace(f'"size": {len(PAGE.encode())}', f'"size": {size}'))
        self.assertIn('"size": 11', after)

    def test_applying_twice_keeps_the_first_original(self):
        self.run_action("apply")
        self.run_action("apply")
        self.assertEqual(read(self.backup / "notifications.html"), PAGE)
        self.assertEqual(read(self.page), warning.filtered_page(PAGE, self.script))
        self.assertEqual(self.run_action("check")[0], 0)

    def test_a_simulator_update_that_replaces_the_page_fails_check_until_apply_runs_again(self):
        self.run_action("apply")
        updated = PAGE.replace("<html>", '<html lang="en">')
        write(self.page, updated)
        write(self.layout, LAYOUT % len(updated.encode()))
        status, out = self.run_action("check")
        self.assertEqual(status, 1)
        self.assertIn("a simulator update probably replaced it", out)
        self.run_action("apply")
        self.assertEqual(read(self.backup / "notifications.html"), updated)
        self.assertEqual(self.run_action("check")[0], 0)

    def test_a_stale_layout_size_fails_check(self):
        self.run_action("apply")
        write(self.layout, LAYOUT % len(PAGE.encode()))
        status, out = self.run_action("check")
        self.assertEqual(status, 1)
        self.assertIn("does not give the page's size on disk", out)

    def test_apply_over_the_earlier_version_keeps_the_true_original_in_the_backup(self):
        legacy = PAGE.replace(warning.BUNDLE_TAG, warning.BUNDLE_TAG + "\r\n\t\t" + warning.LEGACY_TAG)
        write(self.page, legacy)
        write(self.layout, LAYOUT % len(legacy.encode()))
        write(self.backup / "notifications.html", PAGE)
        self.assertEqual(self.run_action("check")[0], 1)
        self.run_action("apply")
        self.assertEqual(read(self.backup / "notifications.html"), PAGE)
        self.assertEqual(read(self.page), warning.filtered_page(PAGE, self.script))
        self.assertEqual(self.run_action("check")[0], 0)
        self.run_action("undo")
        self.assertEqual(read(self.page), PAGE)

    def test_the_earlier_version_without_a_backup_is_refused_instead_of_taken_for_the_original(self):
        legacy = PAGE.replace(warning.BUNDLE_TAG, warning.BUNDLE_TAG + "\r\n\t\t" + warning.LEGACY_TAG)
        write(self.page, legacy)
        status, out = self.run_action("apply")
        self.assertEqual(status, 1)
        self.assertIn("its original is not in", out)
        self.assertFalse((self.backup / "notifications.html").exists())

    def test_apply_and_undo_remove_the_community_package_of_the_earlier_version(self):
        write(self.legacy / "html_ui" / "global" / "flightsim-on-linux-gpu-memory-warning.js", "old")
        self.run_action("apply")
        self.assertFalse(self.legacy.exists())
        write(self.legacy / "manifest.json", "{}")
        status, out = self.run_action("check")
        self.assertEqual(status, 1)
        self.assertIn("is still in the Community folder", out)
        self.run_action("undo")
        self.assertFalse(self.legacy.exists())

    def test_a_new_translation_fails_check_until_apply_runs_again(self):
        self.run_action("apply")
        strings = {"LocalisationPackage": {"Strings": {warning.KEY: "Nova tradução"}}}
        write(self.sim / "Packages" / "fs-base" / "pt-PT.locPak", json.dumps(strings, ensure_ascii=False))
        status, out = self.run_action("check")
        self.assertEqual(status, 1)
        self.assertIn("holds an older filter", out)
        self.run_action("apply")
        self.assertEqual(self.run_action("check")[0], 0)
        self.assertIn("Nova tradução", read(self.page))
        self.assertEqual(read(self.backup / "notifications.html"), PAGE)


MANAGER_HARNESS = """
const shown = [];
class NotificationsManager {
  async NewNotification(notification) { shown.push(["new", notification.title]); }
  UpdateNotificationData(notification) { shown.push(["data", notification.title]); }
  UpdateNotificationText(notification) { shown.push(["text", notification.title]); }
}
globalThis.window = globalThis;
const script = require("fs").readFileSync(process.argv[2], "utf8");
new Function(script)();
setTimeout(async () => {
  window.NotificationsManager = new NotificationsManager();
  await new Promise((resolve) => setTimeout(resolve, 250));
  const manager = window.NotificationsManager;
  await manager.NewNotification({ id: 1, title: "", params: { title: "@fs-base,TT:SYSTEM.NOTIF_LOW_GPU_MEMORY_CAPACITY_DESC" } });
  await manager.NewNotification({ id: 2, title: process.argv[3] });
  await manager.NewNotification({ id: 3, title: "Your flight was saved" });
  manager.UpdateNotificationData({ id: 1, title: "", params: { title: "@fs-base,TT:SYSTEM.NOTIF_LOW_GPU_MEMORY_CAPACITY_DESC" } });
  manager.UpdateNotificationText({ id: 3, title: "Your flight was saved" });
  console.log(JSON.stringify(shown));
}, 50);
"""


@unittest.skipUnless(shutil.which("node"), "needs node to run the filter script")
class FilterCase(unittest.TestCase):
    def test_the_filter_avoids_syntax_the_simulator_javascript_engine_rejects(self):
        with tempfile.TemporaryDirectory() as folder:
            script = warning.filter_script(places(fake_sim(Path(folder) / "sim"), Path(folder), Path(folder)))
        for token in ("??", "?."):
            self.assertNotIn(token, script)

    def test_the_filter_drops_the_warning_by_key_and_by_text_and_lets_other_notifications_through(self):
        with tempfile.TemporaryDirectory() as folder:
            sim = fake_sim(Path(folder) / "sim")
            script = Path(folder) / "filter.js"
            harness = Path(folder) / "harness.js"
            script.write_text(warning.filter_script(places(sim, Path(folder), Path(folder))), encoding="utf-8")
            harness.write_text(MANAGER_HARNESS, encoding="utf-8")
            result = subprocess.run(["node", str(harness), str(script), PORTUGUESE], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [["new", "Your flight was saved"], ["text", "Your flight was saved"]])


if __name__ == "__main__":
    unittest.main()
