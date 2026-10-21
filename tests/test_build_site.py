import contextlib
import io
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tools import build_site


class AssetVersionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.site = Path(self.temp.name) / "site"
        shutil.copytree(build_site.SITE, self.site)
        self.out = Path(self.temp.name) / "docs"

    def tearDown(self):
        self.temp.cleanup()

    def linked_versions(self):
        with contextlib.redirect_stdout(io.StringIO()):
            build_site.build(self.site, self.out)
        page = (self.out / "index.html").read_text()
        return dict(re.findall(r'assets/(style\.css|copy\.js)\?v=([0-9a-f]+)"', page))

    def test_every_page_links_each_asset_with_a_version(self):
        self.assertEqual(set(self.linked_versions()), {"style.css", "copy.js"})

    def test_the_version_changes_only_for_the_asset_that_changed(self):
        before = self.linked_versions()
        with (self.site / "assets" / "style.css").open("a") as style:
            style.write("\n.changed { color: red; }\n")
        after = self.linked_versions()
        self.assertNotEqual(before["style.css"], after["style.css"])
        self.assertEqual(before["copy.js"], after["copy.js"])


if __name__ == "__main__":
    unittest.main()
