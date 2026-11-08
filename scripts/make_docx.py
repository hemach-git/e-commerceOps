"""Convert docs/scope.md and docs/estimates_risks.md to .docx (headings, bullets, tables, code)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.shared import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def add_inline(par, text: str) -> None:
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if part.startswith("**") and part.endswith("**"):
            par.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`"):
            r = par.add_run(part[1:-1])
            r.font.name = "Consolas"
        elif part:
            par.add_run(part)


def _png_size(path: Path) -> tuple[int, int]:
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def _new_section(doc, landscape: bool) -> None:
    prev = doc.sections[-1]
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    w, h = sorted((prev.page_width, prev.page_height))
    sec.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
    sec.page_width, sec.page_height = (h, w) if landscape else (w, h)


def add_diagram(doc, img: Path) -> None:
    """Fit a rendered diagram as large as possible; wide diagrams get their own landscape page."""
    w, h = _png_size(img)
    wide = w / h > 1.25
    if wide:
        _new_section(doc, landscape=True)
    sec = doc.sections[-1]
    max_w = sec.page_width - sec.left_margin - sec.right_margin
    max_h = sec.page_height - sec.top_margin - sec.bottom_margin - Inches(0.6)
    scale = min(max_w / w, max_h / h)
    doc.add_picture(str(img), width=int(w * scale), height=int(h * scale))
    if wide:
        _new_section(doc, landscape=False)


def convert(md_path: Path) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)
    for sec in doc.sections:  # narrower margins -> larger diagrams
        sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Inches(0.75)
    lines = md_path.read_text(encoding="utf-8").splitlines()
    i = 0
    n_mermaid = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("```"):
            lang = line[3:].strip()
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i]); i += 1
            if lang == "mermaid":
                n_mermaid += 1
            img = md_path.parent / "img" / f"{md_path.stem}_{n_mermaid}.png"
            if lang == "mermaid" and img.exists():  # rendered by scripts/render_mermaid.py
                add_diagram(doc, img)
            else:
                p = doc.add_paragraph()
                r = p.add_run("\n".join(code)); r.font.name = "Consolas"; r.font.size = Pt(8.5)
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            ncol = max(len(r) for r in rows)
            t = doc.add_table(rows=len(rows), cols=ncol)
            t.style = "Light Grid Accent 1"
            for ri, r in enumerate(rows):
                for ci in range(ncol):
                    cell = t.cell(ri, ci)
                    cell.text = ""
                    add_inline(cell.paragraphs[0], r[ci] if ci < len(r) else "")
                    if ri == 0:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
            continue
        elif m := re.match(r"^(#{1,4})\s+(.*)", line):
            doc.add_heading(m.group(2), level=min(len(m.group(1)) - 1, 3) if len(m.group(1)) > 1 else 0)
        elif re.match(r"^\s*[-*]\s+", line):
            add_inline(doc.add_paragraph(style="List Bullet"), re.sub(r"^\s*[-*]\s+", "", line))
        elif re.match(r"^\s*\d+\.\s+", line):
            add_inline(doc.add_paragraph(style="List Number"), re.sub(r"^\s*\d+\.\s+", "", line))
        elif line.strip():
            add_inline(doc.add_paragraph(), line)
        i += 1
    out = md_path.with_suffix(".docx")
    doc.save(out)
    return out


if __name__ == "__main__":
    ok = True
    for name in sys.argv[1:] or ("scope.md", "estimates_risks.md", "architecture.md"):
        out = convert(DOCS / name)
        d = Document(out)  # re-open to verify
        size = out.stat().st_size
        print(f"{out.name}: {size} bytes, {len(d.paragraphs)} paragraphs, {len(d.tables)} tables, "
              f"{len(d.inline_shapes)} images")
        ok &= size > 0 and len(d.paragraphs) > 0
    sys.exit(0 if ok else 1)
