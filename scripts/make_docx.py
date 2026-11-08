"""Convert docs/scope.md and docs/estimates_risks.md to .docx (headings, bullets, tables, code)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt

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


def convert(md_path: Path) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)
    lines = md_path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i]); i += 1
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
    for name in ("scope.md", "estimates_risks.md"):
        out = convert(DOCS / name)
        d = Document(out)  # re-open to verify
        size = out.stat().st_size
        print(f"{out.name}: {size} bytes, {len(d.paragraphs)} paragraphs, {len(d.tables)} tables")
        ok &= size > 0 and len(d.paragraphs) > 0
    sys.exit(0 if ok else 1)
