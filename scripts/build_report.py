"""
Script to build and update Mini_Project_Report.docx with drafted chapters.
Strictly adheres to AKTU / Lloyd Institute of Engineering & Technology guidelines:
- Times New Roman throughout
- Heading 1: 15pt, Bold
- Heading 2: 13pt, Bold
- Heading 3: 11-12pt, Bold
- Normal: 12pt, Regular, Justified, 1.5 line spacing
"""

import os
import re
import docx
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

def set_run_font(run, name="Times New Roman", size=12, bold=False, italic=False):
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic

def format_paragraph(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=6):
    p.alignment = align
    p.paragraph_format.line_spacing = line_spacing
    p.paragraph_format.space_after = Pt(space_after)

def insert_chapter_1(doc_path="Mini_Project_Report.docx", draft_path="report_drafts/chapter1_introduction.md"):
    if not os.path.exists(doc_path):
        raise FileNotFoundError(f"{doc_path} not found")
    if not os.path.exists(draft_path):
        raise FileNotFoundError(f"{draft_path} not found")

    with open(draft_path, "r", encoding="utf-8") as f:
        draft_text = f.read()

    doc = docx.Document(doc_path)

    # Locate CHAPTER 1: INTRODUCTION
    target_idx = None
    for i, p in enumerate(doc.paragraphs):
        if "CHAPTER 1: INTRODUCTION" in p.text:
            target_idx = i
            break

    if target_idx is None:
        raise ValueError("Could not find 'CHAPTER 1: INTRODUCTION' in document")

    print(f"Found Chapter 1 at paragraph index {target_idx}")

    # Parse sections from markdown
    lines = draft_text.splitlines()
    
    # We want to insert paragraphs right after target_idx
    # In python-docx, insert_paragraph_before() allows controlled insertion.
    # We will find the paragraph right after target_idx or the next heading.
    next_p = doc.paragraphs[target_idx + 1] if target_idx + 1 < len(doc.paragraphs) else None

    current_p_target = next_p

    for line in lines:
        line_str = line.strip()
        if not line_str or line_str.startswith("# CHAPTER 1") or line_str == "---":
            continue

        if line_str.startswith("## "):
            # Heading 2 (e.g., 1.1 Background)
            title = line_str[3:].strip()
            p = current_p_target.insert_paragraph_before(title, style='Heading 2')
            format_paragraph(p, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.15, space_after=6)
            for r in p.runs:
                set_run_font(r, name="Times New Roman", size=13, bold=True)
        elif line_str.startswith("### "):
            # Heading 3 (e.g., 1.1.1 Sub-topic)
            title = line_str[4:].strip()
            p = current_p_target.insert_paragraph_before(title, style='Heading 3')
            format_paragraph(p, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.15, space_after=4)
            for r in p.runs:
                set_run_font(r, name="Times New Roman", size=11.5, bold=True)
        elif line_str.startswith("* ") or line_str.startswith("- "):
            # Bullet point
            item_text = line_str[2:].strip()
            p = current_p_target.insert_paragraph_before()
            p.paragraph_format.left_indent = Inches(0.25)
            format_paragraph(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=4)
            # Add bullet symbol
            bullet_run = p.add_run("•  ")
            set_run_font(bullet_run, name="Times New Roman", size=12, bold=False)
            # handle bold markdown **...**
            parts = re.split(r'(\*\*.*?\*\*)', item_text)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    r = p.add_run(part[2:-2])
                    set_run_font(r, name="Times New Roman", size=12, bold=True)
                elif part:
                    r = p.add_run(part)
                    set_run_font(r, name="Times New Roman", size=12, bold=False)
        elif re.match(r'^\d+\.\s', line_str):
            # Numbered list
            p = current_p_target.insert_paragraph_before()
            format_paragraph(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=4)
            parts = re.split(r'(\*\*.*?\*\*)', line_str)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    r = p.add_run(part[2:-2])
                    set_run_font(r, name="Times New Roman", size=12, bold=True)
                elif part:
                    r = p.add_run(part)
                    set_run_font(r, name="Times New Roman", size=12, bold=False)
        else:
            # Regular body paragraph
            p = current_p_target.insert_paragraph_before()
            format_paragraph(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=6)
            parts = re.split(r'(\*\*.*?\*\*)', line_str)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    r = p.add_run(part[2:-2])
                    set_run_font(r, name="Times New Roman", size=12, bold=True)
                elif part:
                    r = p.add_run(part)
                    set_run_font(r, name="Times New Roman", size=12, bold=False)

    doc.save(doc_path)
    print(f"Successfully inserted Chapter 1 into {doc_path}!")

if __name__ == "__main__":
    insert_chapter_1()
