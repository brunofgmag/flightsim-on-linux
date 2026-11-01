import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "site" / "pages"
ADDONS = ROOT / "addons"
NO_PAGE = {"fslabs-a321-textures"}
NOT_LISTED = {"addons.html", "index.html", "start.html", "ai.html"}
GUARDED = (PAGES, ADDONS, ROOT / "shims", ROOT / "tools")
FORBIDDEN = (
    ("the owner's home folder", re.compile(r"brunom", re.I)),
    ("a games folder of the owner", re.compile(r"~/Jogos")),
    ("a prefix folder of the owner", re.compile(r"Prefixos")),
    ("the S: drive pattern", re.compile(r"\^S:")),
    ("a Z:\\home path", re.compile(r"Z:\\{1,4}home", re.I)),
    ("the owner's first name", re.compile(r"Bruno")),
    ("an empty word", re.compile(r"\b(seamless|robust|leverage)", re.I)),
)
LINK = re.compile(r'href="([A-Za-z0-9_.-]+\.html)(?:#[^"]*)?"')


def text_files(folder):
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        data = path.read_bytes()
        if b"\0" not in data[:4096]:
            yield path, data.decode(errors="replace")


def page_names():
    return {path.name for path in PAGES.glob("*.html")}


def add_on_folders():
    return {path.name for path in ADDONS.iterdir() if path.is_dir()}


def add_on_info(folder):
    return json.loads((ADDONS / folder / "addon.json").read_text())


def with_script(name):
    return {folder for folder in add_on_folders() if (ADDONS / folder / name).is_file() and os.access(ADDONS / folder / name, os.X_OK)}


def with_field(name):
    return {folder for folder in add_on_folders() if (ADDONS / folder / "addon.json").is_file() and add_on_info(folder).get(name)}


def named_in(page, command):
    return set(re.findall(rf"(?:flightsim-on-linux |<code>)addon {command} ([a-z0-9][a-z0-9-]*)", (PAGES / page).read_text()))


class SiteGuardTest(unittest.TestCase):
    def test_nothing_personal_and_no_empty_words_in_pages_or_scripts(self):
        offenders = []
        for folder in GUARDED:
            for path, text in text_files(folder):
                for number, line in enumerate(text.splitlines(), 1):
                    offenders += [f"{path.relative_to(ROOT)}:{number}: {label}" for label, pattern in FORBIDDEN if pattern.search(line)]
        self.assertEqual(offenders, [])

    def test_every_internal_link_in_a_page_resolves(self):
        broken = []
        for path in sorted(PAGES.glob("*.html")):
            broken += [f"{path.name} -> {target}" for target in LINK.findall(path.read_text()) if not (PAGES / target).is_file() and target != "index.html"]
        self.assertEqual(broken, [])

    def test_every_add_on_folder_has_a_page_of_the_same_id(self):
        missing = sorted(folder for folder in add_on_folders() - NO_PAGE if f"{folder}.html" not in page_names())
        self.assertEqual(missing, [])

    def test_the_add_on_list_links_every_page(self):
        linked = set(LINK.findall((PAGES / "addons.html").read_text()))
        self.assertEqual(sorted(page_names() - NOT_LISTED - linked), [])


class PageListsTest(unittest.TestCase):
    def test_the_installers_page_names_each_add_on_with_an_install_script_and_no_other(self):
        self.assertEqual(named_in("installers.html", "install"), with_script("install"))

    def test_the_shortcuts_page_names_each_add_on_with_menu_entries_and_no_other(self):
        self.assertEqual(named_in("shortcuts-and-watchers.html", "shortcut"), with_field("shortcuts"))

    def test_the_shortcuts_page_names_each_add_on_with_a_watcher_and_no_other(self):
        self.assertEqual(named_in("shortcuts-and-watchers.html", "watcher"), with_field("watcher"))


if __name__ == "__main__":
    unittest.main()
