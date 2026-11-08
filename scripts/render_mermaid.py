"""Render ```mermaid blocks of a Markdown file to PNGs (docs/img/<stem>_<n>.png).

Rendering is local: Playwright drives the installed Chrome; mermaid.js is loaded from a pinned CDN URL
(only the library is downloaded - diagram text never leaves the machine).
    python scripts/render_mermaid.py docs/architecture.md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
MERMAID_JS = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"


def mermaid_blocks(md: str) -> list[str]:
    return re.findall(r"```mermaid\n(.*?)```", md, flags=re.S)


def render(md_path: Path) -> list[Path]:
    blocks = mermaid_blocks(md_path.read_text(encoding="utf-8"))
    out_dir = md_path.parent / "img"
    out_dir.mkdir(exist_ok=True)
    outs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(device_scale_factor=2, viewport={"width": 1400, "height": 900})
        page.set_content(f"<html><body style='background:white;margin:0'><div id='c'></div>"
                         f"<script src='{MERMAID_JS}'></script></body></html>")
        page.wait_for_function("window.mermaid !== undefined", timeout=30000)
        page.evaluate("mermaid.initialize({startOnLoad:false, theme:'default', securityLevel:'strict'})")
        for i, src in enumerate(blocks, 1):
            page.evaluate(
                """async (src) => { const {svg} = await mermaid.render('d' + Date.now(), src);
                   document.getElementById('c').innerHTML = '<div id="w" style="display:inline-block;padding:16px;background:white">' + svg + '</div>'; }""",
                src)
            target = out_dir / f"{md_path.stem}_{i}.png"
            page.locator("#w").screenshot(path=str(target))
            outs.append(target)
        browser.close()
    return outs


if __name__ == "__main__":
    for f in render(ROOT / sys.argv[1]):
        print(f.relative_to(ROOT), f.stat().st_size, "bytes")
