"""MkDocs hooks for the GitHub Pages site (mkdocs.yml).

- The home page is README.md, and stability.md is STABILITY.md, with links into docs/ made
  relative so they stay on the site.
- Links that leave docs/ (examples, AGENTS.md, ...) point at the file on GitHub.
- Release notes are added to the nav, newest first, so a release needs no mkdocs.yml change;
  versions not released yet are titled "(planned)".
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent
BLOB = "https://github.com/Ingeniator/logwarden/blob/main/"


def _version(path: Path) -> tuple[int, ...]:
    return tuple(int(p) for p in path.stem.lstrip("v").split("."))


def _title(path: Path) -> str:
    """'v0.9.0 (planned)' until the notes say Released, so planned versions don't look shipped."""
    status = next((ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("**Status:**")), "")
    return path.stem if "Released" in status else f"{path.stem} (planned)"


def on_config(config):
    releases = sorted((ROOT / "docs/releases").glob("v*.md"), key=_version, reverse=True)
    config["nav"].append({"Release notes": [{_title(p): f"releases/{p.name}"} for p in releases]})
    return config


def on_page_markdown(markdown, page, config, files):
    if page.file.src_uri == "index.md":
        markdown = (ROOT / "README.md").read_text(encoding="utf-8")
        markdown = markdown.replace(BLOB + "docs/", "")
    elif page.file.src_uri == "stability.md":
        markdown = (ROOT / "STABILITY.md").read_text(encoding="utf-8")
        markdown = markdown.replace("](docs/", "](")
    # ../../examples/... from docs/recipes, and similar links out of docs/
    depth = page.file.src_uri.count("/")
    up = "../" * (depth + 1)
    if depth:
        markdown = re.sub(r"\]\(" + re.escape(up) + r"([^)]+)\)", lambda m: f"]({BLOB}{m.group(1)})", markdown)
    return markdown
