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
    ("proton-fenix.html", "Proton Fenix"),
    ("rules.html", "Ground rules"),
    ("addons.html", "Add-ons"),
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


def build():
    layout = Template((SITE / "layout.html").read_text())
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SITE / "assets", OUT / "assets")
    for static in ("llms.txt",):
        shutil.copy(SITE / static, OUT / static)
    (OUT / ".nojekyll").write_text("")
    for page in sorted((SITE / "pages").glob("*.html")):
        meta, body = read_page(page)
        (OUT / page.name).write_text(layout.substitute(
            title=html.escape(meta["title"]),
            description=html.escape(meta["description"]),
            nav=nav_for(page.name),
            body=body.strip(),
        ))
        print(f"built {page.name}")


if __name__ == "__main__":
    sys.exit(build())
