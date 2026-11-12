import hashlib
import html
import shutil
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = ROOT / "docs"
NAV = (
    ("index.html", "Home"),
    ("start.html", "Start here"),
    ("proton-fs2024.html", "Proton FS2024"),
    ("launch-options.html", "Launch options"),
    ("rules.html", "Ground rules"),
    ("addons.html", "Add-ons"),
    ("installers.html", "Installing add-ons"),
    ("shortcuts-and-watchers.html", "Shortcuts and watchers"),
    ("ai.html", "With an AI"),
)


def read_page(path):
    head, _, body = path.read_text().partition("\n---\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines() if line.strip())
    return meta, body


def nav_for(name):
    links = []
    for target, label in NAV:
        current = ' aria-current="page"' if target == name else ""
        links.append(f'<a href="{target}"{current}>{html.escape(label)}</a>')
    return "\n        ".join(links)


def asset_url(site, name):
    version = hashlib.sha256((site / "assets" / name).read_bytes()).hexdigest()[:12]
    return f"assets/{name}?v={version}"


def build(site=SITE, out=OUT):
    layout = Template((site / "layout.html").read_text())
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(site / "assets", out / "assets")
    for static in ("llms.txt",):
        shutil.copy(site / static, out / static)
    (out / ".nojekyll").write_text("")
    for page in sorted((site / "pages").glob("*.html")):
        meta, body = read_page(page)
        (out / page.name).write_text(layout.substitute(
            title=html.escape(meta["title"]),
            description=html.escape(meta["description"]),
            style=asset_url(site, "style.css"),
            script=asset_url(site, "copy.js"),
            nav=nav_for(page.name),
            body=body.strip(),
        ))
        print(f"built {page.name}")


if __name__ == "__main__":
    sys.exit(build())
