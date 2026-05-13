from __future__ import annotations

import re
from pathlib import Path


IMAGE_LINK_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")


def test_readme_images_use_absolute_urls_for_astrbot_dashboard():
    readme_path = Path(__file__).resolve().parents[1] / "readme.md"
    content = readme_path.read_text(encoding="utf-8")

    image_urls = IMAGE_LINK_RE.findall(content)

    assert image_urls
    assert [
        url
        for url in image_urls
        if not url.startswith(("https://", "http://"))
    ] == []
