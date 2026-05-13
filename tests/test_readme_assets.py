from __future__ import annotations

import re
from pathlib import Path


IMAGE_LINK_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")


def test_readme_images_use_absolute_urls_for_astrbot_dashboard():
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
    content = readme_path.read_text(encoding="utf-8")

    image_urls = IMAGE_LINK_RE.findall(content)

    assert image_urls
    assert [
        url
        for url in image_urls
        if not url.startswith(("https://", "http://"))
    ] == []


def test_repository_uses_astrbot_dashboard_readme_name():
    root = Path(__file__).resolve().parents[1]
    root_names = {path.name for path in root.iterdir()}

    assert "README.md" in root_names
    assert "readme.md" not in root_names
