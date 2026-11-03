"""
compile_full_report.py
----------------------
Compiles the complete Mini Project Report into Mini_Project_Report.docx based on
Mini_Project_Report_Format.docx, strictly adhering to AKTU / Lloyd Institute guidelines:
- Font: Times New Roman throughout
- Heading 1: 15 pt, Bold, Uppercase, Black
- Heading 2: 13 pt, Bold, Black
- Heading 3: 12 pt, Bold, Black
- Body Text: 12 pt, Regular, Justified, 1.5 line spacing, 6 pt space after, Black
- Tables: 10-11 pt, Times New Roman, Black borders and text
- Strict Black and White only: RGBColor(0, 0, 0)
- Page breaks before each major chapter and section
"""

import os
import re
import docx
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

BLACK = RGBColor(0, 0, 0)

def set_font(run, name="Times New Roman", size=12, bold=False, italic=False, color=BLACK):
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = color

def format_p(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=6, space_before=0):
    p.alignment = align
    p.paragraph_format.line_spacing = line_spacing
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(space_before)

def add_styled_paragraph(doc, text="", align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=6):
    p = doc.add_paragraph()
    format_p(p, align, line_spacing, space_after)
    if text:
        parts = re.split(r'(\*\*.*?\*\*|\*.*?\*)', text)
        for part in parts:
            if part.startswith("**") and part.endswith("**"):
                r = p.add_run(part[2:-2])
                set_font(r, name="Times New Roman", size=12, bold=True, color=BLACK)
            elif part.startswith("*") and part.endswith("*") and not part.startswith("**"):
                r = p.add_run(part[1:-1])
                set_font(r, name="Times New Roman", size=12, italic=True, color=BLACK)
            elif part:
                r = p.add_run(part)
                set_font(r, name="Times New Roman", size=12, bold=False, color=BLACK)
    return p

def add_heading_1(doc, text):
    p = doc.add_paragraph()
    format_p(p, align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.15, space_after=12, space_before=12)
    p.style = doc.styles['Heading 1']
    r = p.add_run(text.upper())
    set_font(r, name="Times New Roman", size=15, bold=True, color=BLACK)
    return p

def add_heading_2(doc, text):
    p = doc.add_paragraph()
    format_p(p, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.15, space_after=6, space_before=10)
    p.style = doc.styles['Heading 2']
    r = p.add_run(text)
    set_font(r, name="Times New Roman", size=13, bold=True, color=BLACK)
    return p

def add_heading_3(doc, text):
    p = doc.add_paragraph()
    format_p(p, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.15, space_after=4, space_before=6)
    p.style = doc.styles['Heading 3']
    r = p.add_run(text)
    set_font(r, name="Times New Roman", size=12, bold=True, color=BLACK)
    return p

def add_bullet_point(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.25)
    format_p(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=4)
    b_run = p.add_run("•  ")
    set_font(b_run, name="Times New Roman", size=12, bold=False, color=BLACK)
    parts = re.split(r'(\*\*.*?\*\*|\*.*?\*)', text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            r = p.add_run(part[2:-2])
            set_font(r, name="Times New Roman", size=12, bold=True, color=BLACK)
        elif part.startswith("*") and part.endswith("*") and not part.startswith("**"):
            r = p.add_run(part[1:-1])
            set_font(r, name="Times New Roman", size=12, italic=True, color=BLACK)
        elif part:
            r = p.add_run(part)
            set_font(r, name="Times New Roman", size=12, bold=False, color=BLACK)
    return p

def add_code_block(doc, code_text):
    for line in code_text.splitlines():
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.4)
        format_p(p, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.0, space_after=1, space_before=0)
        r = p.add_run(line if line else " ")
        set_font(r, name="Courier New", size=9.5, bold=False, color=BLACK)

def add_markdown_table(doc, table_lines):
    rows_data = []
    for line in table_lines:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("| :") or line_clean.startswith("|:--"):
            continue
        cells = [c.strip() for c in line_clean.strip("|").split("|")]
        rows_data.append(cells)
    
    if not rows_data:
        return
    
    cols_count = len(rows_data[0])
    table = doc.add_table(rows=len(rows_data), cols=cols_count)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    
    for r_idx, row in enumerate(rows_data):
        is_header = (r_idx == 0)
        for c_idx, cell_text in enumerate(row):
            if c_idx >= cols_count:
                continue
            cell = table.cell(r_idx, c_idx)
            cell.text = ""
            p = cell.paragraphs[0]
            format_p(p, align=WD_ALIGN_PARAGRAPH.LEFT if not is_header else WD_ALIGN_PARAGRAPH.CENTER, 
                     line_spacing=1.15, space_after=3, space_before=3)
            # handle bold formatting inside table
            parts = re.split(r'(\*\*.*?\*\*)', cell_text)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    r = p.add_run(part[2:-2])
                    set_font(r, name="Times New Roman", size=10, bold=True, color=BLACK)
                elif part:
                    r = p.add_run(part)
                    set_font(r, name="Times New Roman", size=10, bold=is_header, color=BLACK)
            
            # Subtle cell borders
            tcPr = cell._tc.get_or_add_tcPr()
            tcBorders = parse_xml(
                f'<w:tcBorders {nsdecls("w")}>\n'
                f'  <w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/>\n'
                f'  <w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/>\n'
                f'  <w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>\n'
                f'  <w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/>\n'
                f'</w:tcBorders>'
            )
            tcPr.append(tcBorders)

    # Empty paragraph after table
    p = doc.add_paragraph()
    format_p(p, space_after=6)

def parse_and_insert_markdown(doc, md_text):
    lines = md_text.splitlines()
    in_code_block = False
    code_lines = []
    in_table = False
    table_lines = []

    for line in lines:
        line_clean = line.strip()

        # Handle code block toggle
        if line_clean.startswith("```"):
            if in_code_block:
                add_code_block(doc, "\n".join(code_lines))
                code_lines = []
                in_code_block = False
            else:
                if in_table:
                    add_markdown_table(doc, table_lines)
                    table_lines = []
                    in_table = False
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Handle tables
        if line_clean.startswith("|") and line_clean.endswith("|"):
            in_table = True
            table_lines.append(line_clean)
            continue
        else:
            if in_table:
                add_markdown_table(doc, table_lines)
                table_lines = []
                in_table = False

        if not line_clean or line_clean == "---":
            continue

        # Headings
        if line_clean.startswith("# "):
            # Major Chapter heading
            add_heading_1(doc, line_clean[2:].strip())
        elif line_clean.startswith("## "):
            # Major Section
            add_heading_2(doc, line_clean[3:].strip())
        elif line_clean.startswith("### "):
            # Sub-section
            add_heading_3(doc, line_clean[4:].strip())
        elif line_clean.startswith("#### "):
            # Sub-sub-section
            add_heading_3(doc, line_clean[5:].strip())
        elif line_clean.startswith("* ") or line_clean.startswith("- "):
            add_bullet_point(doc, line_clean[2:].strip())
        elif re.match(r'^\d+\.\s', line_clean):
            add_styled_paragraph(doc, line_clean)
        else:
            add_styled_paragraph(doc, line_clean)

def build_complete_report(format_path="Mini_Project_Report_Format.docx", output_path="Mini_Project_Report.docx"):
    print(f"Loading base format: {format_path}")
    doc = docx.Document(format_path)

    # 1. Update Title on Cover Page
    for p in doc.paragraphs[:15]:
        if "PROJECT TITLE" in p.text:
            p.text = ""
            format_p(p, align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.15, space_after=12, space_before=12)
            r = p.add_run("DESIGN AND IMPLEMENTATION OF AN LLM-NATIVE HEADLESS BROWSER ENGINE WITHOUT EXTERNAL BROWSER DEPENDENCIES")
            set_font(r, name="Cambria", size=15, bold=True, color=BLACK)
            break

    # 2. Update Certificate
    for p in doc.paragraphs[15:30]:
        if "[Title of the Project]" in p.text:
            p.text = p.text.replace(
                "[Title of the Project]",
                "Design and Implementation of an LLM-Native Headless Browser Engine Without External Browser Dependencies"
            )
            for r in p.runs:
                set_font(r, name="Times New Roman", size=12, bold=False, color=BLACK)
            format_p(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=6)
        if "Place: [City]" in p.text:
            p.text = p.text.replace("[City]", "Greater Noida")
            for r in p.runs:
                set_font(r, name="Times New Roman", size=12, bold=False, color=BLACK)

    # 3. Update Declaration
    for p in doc.paragraphs[25:40]:
        if "[Title of the Project]" in p.text:
            p.text = p.text.replace(
                "[Title of the Project]",
                "Design and Implementation of an LLM-Native Headless Browser Engine Without External Browser Dependencies"
            )
            for r in p.runs:
                set_font(r, name="Times New Roman", size=12, bold=False, color=BLACK)
            format_p(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=6)

    # 4. Update Acknowledgement
    for p in doc.paragraphs[35:45]:
        if "[Guide Name]" in p.text:
            p.text = p.text.replace("[Guide Name]", "Project Guide").replace("[Designation]", "Assistant Professor")
            for r in p.runs:
                set_font(r, name="Times New Roman", size=12, bold=False, color=BLACK)
            format_p(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=6)

    # 5. Populate Abstract
    with open("report_drafts/abstract_and_preliminaries.md", "r", encoding="utf-8") as f:
        prelim_text = f.read()

    abstract_content = prelim_text.split("## Abstract")[1].split("---")[0].strip()
    
    # Locate ABSTRACT paragraph
    for i, p in enumerate(doc.paragraphs):
        if p.text.strip() == "ABSTRACT":
            # Add abstract body after heading
            next_p = doc.paragraphs[i+1]
            next_p.text = ""
            for para in abstract_content.split("\n\n"):
                p_new = next_p.insert_paragraph_before()
                format_p(p_new, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line_spacing=1.5, space_after=6)
                r = p_new.add_run(para.strip())
                set_font(r, name="Times New Roman", size=12, bold=False, color=BLACK)
            break

    # 6. Populate LIST OF FIGURES & LIST OF TABLES
    figs_content = prelim_text.split("## List of Figures")[1].split("---")[0].strip()
    tables_content = prelim_text.split("## List of Tables")[1].split("---")[0].strip()

    for i, p in enumerate(doc.paragraphs):
        if p.text.strip() == "LIST OF FIGURES":
            next_p = doc.paragraphs[i+1] if i+1 < len(doc.paragraphs) else None
            if next_p:
                for fig_line in figs_content.splitlines():
                    if fig_line.strip():
                        p_fig = next_p.insert_paragraph_before()
                        format_p(p_fig, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.3, space_after=3)
                        r = p_fig.add_run(fig_line.strip())
                        set_font(r, name="Times New Roman", size=12, bold=False, color=BLACK)
            break

    for i, p in enumerate(doc.paragraphs):
        if p.text.strip() == "LIST OF TABLES":
            next_p = doc.paragraphs[i+1] if i+1 < len(doc.paragraphs) else None
            if next_p:
                for tbl_line in tables_content.splitlines():
                    if tbl_line.strip():
                        p_tbl = next_p.insert_paragraph_before()
                        format_p(p_tbl, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.3, space_after=3)
                        r = p_tbl.add_run(tbl_line.strip())
                        set_font(r, name="Times New Roman", size=12, bold=False, color=BLACK)
            break

    # 7. Populate LIST OF ABBREVIATIONS Table
    abbr_data = [
        ("AI", "Artificial Intelligence"),
        ("ALPN", "Application-Layer Protocol Negotiation"),
        ("API", "Application Programming Interface"),
        ("AST", "Abstract Syntax Tree"),
        ("CDP", "Chrome DevTools Protocol"),
        ("CLI", "Command Line Interface"),
        ("CSS", "Cascading Style Sheets"),
        ("DFD", "Data Flow Diagram"),
        ("DOM", "Document Object Model"),
        ("DRM", "Digital Rights Management"),
        ("ESM", "ECMAScript Modules"),
        ("GPU", "Graphics Processing Unit"),
        ("HTTP", "Hypertext Transfer Protocol"),
        ("IPC", "Inter-Process Communication"),
        ("JSON", "JavaScript Object Notation"),
        ("LLM", "Large Language Model"),
        ("NFR", "Non-Functional Requirement"),
        ("NLP", "Natural Language Processing"),
        ("RAM", "Random Access Memory"),
        ("REST", "Representational State Transfer"),
        ("RFC", "Request for Comments"),
        ("SPA", "Single Page Application"),
        ("TLS", "Transport Layer Security"),
        ("UML", "Unified Modeling Language"),
        ("URL", "Uniform Resource Locator"),
        ("W3C", "World Wide Web Consortium"),
        ("WASM", "WebAssembly"),
    ]

    # Find Table 2 (abbreviations table)
    if len(doc.tables) >= 3:
        abbr_table = doc.tables[2]
        # Clear existing rows after header
        for r_idx in range(len(abbr_table.rows) - 1, 0, -1):
            tr = abbr_table.rows[r_idx]._tr
            tr.getparent().remove(tr)
        
        # Add all abbreviations
        for abbr, full_form in abbr_data:
            row = abbr_table.add_row()
            c0 = row.cells[0]
            c0.text = ""
            p0 = c0.paragraphs[0]
            format_p(p0, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.15, space_after=2, space_before=2)
            r0 = p0.add_run(abbr)
            set_font(r0, name="Times New Roman", size=11, bold=True, color=BLACK)

            c1 = row.cells[1]
            c1.text = ""
            p1 = c1.paragraphs[0]
            format_p(p1, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.15, space_after=2, space_before=2)
            r1 = p1.add_run(full_form)
            set_font(r1, name="Times New Roman", size=11, bold=False, color=BLACK)

    # 8. Truncate empty chapter headings at the end of the template
    # Find paragraph "CHAPTER 1: INTRODUCTION"
    ch1_idx = None
    for i, p in enumerate(doc.paragraphs):
        if "CHAPTER 1: INTRODUCTION" in p.text:
            ch1_idx = i
            break

    if ch1_idx is not None:
        # Remove paragraphs from ch1_idx to the end
        body_elm = doc._body._element
        for p in doc.paragraphs[ch1_idx:]:
            p_elem = p._element
            if p_elem.getparent() == body_elm:
                body_elm.remove(p_elem)

    # 9. Append Chapters 1 through 7, References, and Appendices with Page Breaks
    chapter_files = [
        ("report_drafts/chapter1_introduction.md", True),
        ("report_drafts/chapter2_literature_review.md", True),
        ("report_drafts/chapter3_system_analysis_and_design.md", True),
        ("report_drafts/chapter4_implementation.md", True),
        ("report_drafts/chapter5_testing.md", True),
        ("report_drafts/chapter6_results_and_discussion.md", True),
        ("report_drafts/chapter7_conclusion_and_future_scope.md", True),
        ("report_drafts/references_and_appendices.md", True),
    ]

    for fpath, add_page_break in chapter_files:
        print(f"Appending {fpath}...")
        if add_page_break:
            doc.add_page_break()
        with open(fpath, "r", encoding="utf-8") as f:
            md_content = f.read()
        parse_and_insert_markdown(doc, md_content)

    # 10. Enforce STRICT Black and White (RGBColor(0, 0, 0)) across the entire document
    print("Enforcing strict black and white typography across all paragraphs and tables...")
    for p in doc.paragraphs:
        for r in p.runs:
            r.font.color.rgb = BLACK
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for r in p.runs:
                        r.font.color.rgb = BLACK

    doc.save(output_path)
    print(f"\n=======================================================")
    print(f"SUCCESS: Complete report compiled into: {output_path}")
    print(f"Total Paragraphs: {len(doc.paragraphs)}")
    print(f"Total Tables: {len(doc.tables)}")
    print(f"=======================================================")

if __name__ == "__main__":
    build_complete_report()

