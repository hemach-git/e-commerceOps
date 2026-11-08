"""Six-slide, simple-English, editable deck. Numbers are read live from the DB and eval results."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LLM_PROVIDER", "mock")

from app.services import analytics, workflow  # noqa: E402

NAVY, TEAL, GREY = RGBColor(0x1F, 0x2A, 0x44), RGBColor(0x0E, 0x8C, 0x7F), RGBColor(0x55, 0x5B, 0x66)


def numbers() -> dict:
    workflow.init_app()
    k = analytics.get_kpis()
    if k["processed"] == 0:  # make sure the deck has real measured numbers
        workflow.run_batch(actor="deck")
        k = analytics.get_kpis()
    ev = json.loads((ROOT / "evals/results/latest.json").read_text(encoding="utf-8"))
    reasons = analytics.quarantine_reasons()[:3]
    return {"k": k, "ev": ev, "reasons": reasons}


def bullets(slide, items, top=1.55, size=20):
    box = slide.shapes.add_textbox(Inches(0.6), Inches(top), Inches(8.8), Inches(4.5))
    tf = box.text_frame
    tf.word_wrap = True
    for i, it in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = "•  " + it
        p.font.size = Pt(size)
        p.font.color.rgb = NAVY
        p.space_after = Pt(10)
    return box


def title(slide, text, sub=None):
    slide.shapes.title.text = text
    tp = slide.shapes.title.text_frame.paragraphs[0]
    tp.font.size, tp.font.bold, tp.font.color.rgb = Pt(32), True, NAVY
    if sub:
        b = slide.shapes.add_textbox(Inches(0.6), Inches(1.05), Inches(8.8), Inches(0.5))
        b.text_frame.text = sub
        b.text_frame.paragraphs[0].font.size = Pt(15)
        b.text_frame.paragraphs[0].font.color.rgb = TEAL


def footer(slide):
    b = slide.shapes.add_textbox(Inches(0.6), Inches(7.0), Inches(8.8), Inches(0.3))
    b.text_frame.text = "All data shown is SYNTHETIC demo data."
    b.text_frame.paragraphs[0].font.size = Pt(10)
    b.text_frame.paragraphs[0].font.color.rgb = GREY


def main() -> int:
    n = numbers()
    k, ev = n["k"], n["ev"]
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10), Inches(7.5)
    layout = prs.slide_layouts[5]  # Title Only: keeps text editable

    s = prs.slides.add_slide(layout)
    title(s, "The problem", "Sellers send messy product listings")
    bullets(s, ["Titles are cluttered: “Gym Shorts w/ Pockets Pack of 2 (Black/Navy, L) - Running Workout”",
                "Sizes and colours use the seller’s own codes (“Lrg”, “Blk/Nvy”)",
                "Some listings make banned claims (“cures anxiety”, “FDA approved”, “replica”)",
                "Cleaning by hand is slow; a bad listing going live is a legal and brand risk"])
    footer(s)

    s = prs.slides.add_slide(layout)
    title(s, "Our solution", "An assistant that cleans, sorts and safety-checks every listing before it goes live")
    bullets(s, ["Cleans titles, sizes, colours and pack counts into one standard format",
                "Puts each product in the right category and checks it has the details that category needs",
                "Fixed safety rules (not AI opinion) decide what is blocked, and why",
                "A person always approves before anything is published"])
    footer(s)

    s = prs.slides.add_slide(layout)
    title(s, "How it works", "Five simple steps")
    steps = ["Seller\nlisting", "Safety\nscreen", "AI reads &\nsorts", "Rule\nchecks", "Draft or\nQuarantine", "Person\napproves"]
    for i, st in enumerate(steps):
        shp = s.shapes.add_shape(MSO_SHAPE.CHEVRON if i else MSO_SHAPE.PENTAGON,
                                 Inches(0.35 + i * 1.55), Inches(2.3), Inches(1.5), Inches(1.3))
        shp.fill.solid()
        shp.fill.fore_color.rgb = TEAL if i in (2,) else NAVY
        shp.line.fill.background()
        tf = shp.text_frame
        tf.text = st
        for p in tf.paragraphs:
            p.font.size, p.font.bold, p.font.color.rgb = Pt(12), True, RGBColor(0xFF, 0xFF, 0xFF)
    bullets(s, ["AI only suggests: every answer is checked against a strict form and retried if wrong",
                "Blocked listings get a clear reason code (e.g. PROHIBITED_KEYWORD)"], top=4.2, size=17)
    footer(s)

    s = prs.slides.add_slide(layout)
    title(s, "Demo results", "Measured on 40 synthetic seller listings and a 26-case test set")
    top = ", ".join(f"{r['reason']} ({r['count']})" for r in n["reasons"])
    bullets(s, [f"{k['processed']} listings processed: {k['drafted'] + k['published']} ready for review or live, "
                f"{k['quarantined']} quarantined ({k['quarantine_rate']:.0%})",
                f"Top quarantine reasons: {top}",
                f"Required details complete on {k['attr_completeness_rate']:.0%} of categorised listings",
                f"Test set: {ev['passed']}/{ev['total']} correct ({ev['pass_rate']:.0%}) using the offline test model; "
                "real-AI accuracy still to be measured",
                "Example: “Men’s Dry-Fit … Pack of 2 (Black/Navy, L) - Running Workout” → "
                "“Men’s Running Shorts, Dry-Fit, Pockets (Pack of 2)”, sizes L, colours Black + Navy"], size=17)
    footer(s)

    s = prs.slides.add_slide(layout)
    title(s, "Safety & trust")
    bullets(s, ["Hidden instructions in seller text (“ignore previous instructions…”) are caught before the AI sees them",
                "Personal details (emails, phone numbers) are blocked and hidden in logs",
                "Nothing goes live without a named person’s approval; blocked listings cannot be approved",
                "Every decision is logged: who, what, when, which AI model, request ID",
                "All demo data is synthetic, no real sellers or customers"])
    footer(s)

    s = prs.slides.add_slide(layout)
    title(s, "Next steps")
    bullets(s, ["Measure accuracy with the real AI model on a larger, real (anonymised) feed sample",
                "Let reviewers fix quarantined listings and send them back through",
                "Smarter banned-word matching (misspellings like “r3plica”), duplicate detection, images",
                "Production: login and roles for reviewers, Postgres database, connect live seller feeds",
                "Success measure: fewer manual hours per 1,000 listings, zero banned claims going live"])
    footer(s)

    out = ROOT / "docs/presentation.pptx"
    prs.save(out)
    count = len(Presentation(out).slides)
    print(f"{out.name}: {count} slides, {out.stat().st_size} bytes")
    return 0 if count == 6 else 1


if __name__ == "__main__":
    sys.exit(main())
