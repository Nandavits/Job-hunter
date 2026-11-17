"""Render the tailored CV and cover letter as .docx (and PDF via LibreOffice, if installed).

The layout matches the hand-made CV: Times New Roman, bold section headings with a rule
underneath, bold role titles, italic blue-grey dates, compact bullets, one A4 page.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .tailor import Tailoring

FONT = "Times New Roman"
DATE_COLOR = RGBColor(0x44, 0x54, 0x6A)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40] or "job"


class _Writer:
    def __init__(self, body_pt: float, margins_cm: tuple[float, float, float, float]):
        self.doc = Document()
        self.body_pt = body_pt
        section = self.doc.sections[0]
        section.page_width, section.page_height = Cm(21.0), Cm(29.7)
        section.top_margin, section.bottom_margin, section.left_margin, section.right_margin = map(Cm, margins_cm)
        style = self.doc.styles["Normal"]
        style.font.name = FONT
        style.font.size = Pt(body_pt)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
        fmt = style.paragraph_format
        fmt.space_before = fmt.space_after = Pt(0)
        fmt.line_spacing = 1.0

    def para(self, runs, *, before=0.0, after=0.0, align=None, keep_next=False):
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        p.paragraph_format.keep_with_next = keep_next
        if align:
            p.alignment = align
        for run in runs:
            text, opts = (run, {}) if isinstance(run, str) else run
            r = p.add_run(text)
            r.font.name = FONT
            r.font.size = Pt(opts.get("size", self.body_pt))
            r.bold = opts.get("bold", False)
            r.italic = opts.get("italic", False)
            if opts.get("color"):
                r.font.color.rgb = opts["color"]
        return p

    def rule_below(self, p, space_pt: int = 1):
        ppr = p._p.get_or_add_pPr()
        bdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in {"w:val": "single", "w:sz": "6", "w:space": str(space_pt), "w:color": "000000"}.items():
            bottom.set(qn(k), v)
        bdr.append(bottom)
        ppr.append(bdr)

    def heading(self, text: str):
        p = self.para([(text, {"bold": True, "size": self.body_pt + 1.5})], before=5, after=2, keep_next=True)
        self.rule_below(p)

    def bullet(self, text: str):
        p = self.doc.add_paragraph(style="List Bullet")
        fmt = p.paragraph_format
        fmt.space_before, fmt.space_after = Pt(0), Pt(0.5)
        fmt.left_indent, fmt.first_line_indent = Cm(0.63), Cm(-0.39)
        r = p.add_run(text)
        r.font.name, r.font.size = FONT, Pt(self.body_pt)


def build_cv(profile: dict, t: Tailoring, path: Path, body_pt: float = 9.5) -> Path:
    w = _Writer(body_pt, (0.88, 0.67, 1.41, 1.41))
    me = profile["personal"]
    name = f"{me['first_name']} {me['last_name']}"
    w.para([(name, {"bold": True, "size": 14})])
    w.para([(t.title, {"size": 10.5})], before=2, after=1)
    contact = [me.get("city") and f"{me['city']}, {me.get('country', '')}".strip(", "), me.get("phone"),
               me.get("email"), me.get("linkedin", "").replace("https://", ""),
               me.get("github", "").replace("https://", "")]
    w.para([" | ".join(c for c in contact if c)])

    w.heading("PROFESSIONAL SUMMARY")
    w.para([t.summary], align=WD_ALIGN_PARAGRAPH.JUSTIFY)

    w.heading("PROFESSIONAL EXPERIENCE")
    for i, exp in enumerate(profile.get("experience", [])):
        w.para([(exp["title"], {"bold": True}), f"  |  {exp['company']}, {exp['location']}"],
               before=4, keep_next=True)
        w.para([(exp["dates"], {"italic": True, "color": DATE_COLOR})], after=1, keep_next=True)
        for k in t.bullet_order.get(i, range(len(exp["bullets"]))):
            w.bullet(exp["bullets"][k]["text"])

    w.heading("TECHNICAL SKILLS")
    skills = profile.get("skills", {})
    for cat in t.skills_order or list(skills):
        w.para([(f"{cat}: ", {"bold": True}), ", ".join(skills[cat])], after=0.5)

    if profile.get("projects"):
        w.heading("PROJECTS")
        for proj in profile["projects"]:
            w.para([(f"{proj['name']} | {proj['stack']}", {"bold": True})], before=1, keep_next=True)
            w.bullet(proj["bullet"])

    w.heading("EDUCATION")
    for edu in profile.get("education", []):
        w.para([(edu["degree"], {"bold": True})])
        w.para([f"{edu['school']}  ·  {edu['dates']}"], after=1)

    if profile.get("languages"):
        w.heading("LANGUAGES")
        w.para(["  ·  ".join(profile["languages"])])
    if profile.get("certifications"):
        w.heading("CERTIFICATIONS")
        w.para(["  ·  ".join(profile["certifications"])])

    path.parent.mkdir(parents=True, exist_ok=True)
    w.doc.core_properties.author = name
    w.doc.core_properties.title = f"{name} – CV"
    w.doc.save(path)
    return path


def build_letter(profile: dict, t: Tailoring, job: dict, path: Path) -> Path:
    w = _Writer(11, (1.41, 1.41, 1.94, 1.94))
    me = profile["personal"]
    name = f"{me['first_name']} {me['last_name']}"
    w.para([(name, {"bold": True, "size": 14})])
    w.para([(t.title, {"size": 10.5})], before=2, after=1)
    contact = [f"{me.get('city', '')}, {me.get('country', '')}".strip(", "), me.get("phone"), me.get("email"),
               me.get("linkedin", "").replace("https://", "")]
    p = w.para([(" | ".join(c for c in contact if c), {"size": 9.5})])
    w.rule_below(p, space_pt=4)

    company = job.get("company") or "Hiring Team"
    w.para([(f"{company} – Hiring Team", {"bold": True})], before=18)
    if job.get("location"):
        w.para([job["location"].split(",")[0]])
    today = date.today()
    w.para([f"{me.get('city', 'Berlin')}, {today.day} {today.strftime('%B %Y')}"],
           align=WD_ALIGN_PARAGRAPH.RIGHT, before=6, after=12)
    w.para([(f"Application – {job.get('title', '')}", {"bold": True})], after=12)
    w.para([f"Dear {company} Hiring Team,"], after=8)
    for text in t.letter_paragraphs:
        p = w.para([text], after=8, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
        p.paragraph_format.line_spacing = 1.15
    w.para(["Kind regards,"], before=4, after=20)
    w.para([name])

    path.parent.mkdir(parents=True, exist_ok=True)
    w.doc.core_properties.author = name
    w.doc.save(path)
    return path


# --------------------------------------------------------------------------- PDF
def soffice_path() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def to_pdf(docx_path: Path) -> Path | None:
    exe = soffice_path()
    if not exe:
        return None
    try:
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent),
                        str(docx_path)], check=True, capture_output=True, timeout=120)
    except (subprocess.SubprocessError, OSError):
        return None
    pdf = docx_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def pdf_pages(pdf: Path) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))


def make_documents(profile: dict, t: Tailoring, job: dict, out_root: Path, make_pdf: bool = True) -> dict:
    """Create CV + letter in output/<date>_<company>_<title>/. Shrinks the CV font until it fits one page."""
    me = profile["personal"]
    who = _slug(f"{me['first_name'].split()[0]}_{me['last_name']}")
    folder = out_root / f"{date.today().isoformat()}_{_slug(job.get('company', ''))}_{_slug(job.get('title', ''))}"
    company = _slug(job.get("company", "Company"))
    cv = folder / f"CV_{who}_{company}.docx"
    letter = folder / f"Cover_Letter_{who}_{company}.docx"

    result = {"folder": str(folder), "cv_docx": str(cv), "letter_docx": str(letter)}
    build_letter(profile, t, job, letter)
    for size in (9.5, 9.25, 9.0, 8.75):
        build_cv(profile, t, cv, body_pt=size)
        if not make_pdf:
            break
        pdf = to_pdf(cv)
        if pdf is None:
            break
        result["cv_pdf"] = str(pdf)
        if pdf_pages(pdf) <= 1:
            break
    if make_pdf:
        pdf = to_pdf(letter)
        if pdf:
            result["letter_pdf"] = str(pdf)

    notes = folder / "NOTES.md"
    notes.write_text(
        f"# {job.get('title')} – {job.get('company')}\n\n"
        f"- Apply: {job.get('apply_url') or job.get('url')}\n"
        f"- Tailoring engine: {t.engine}\n"
        f"- CV title used: {t.title}\n\n"
        "## Why it fits\n" + "".join(f"- {n}\n" for n in t.fit_notes) +
        "\n## Gaps to be ready for in the interview\n" + ("".join(f"- {g}\n" for g in t.gaps) or "- none found\n") +
        "\n## Before you submit\n- Read the CV and letter once.\n- Check every form answer.\n"
        "- You click Submit yourself.\n", encoding="utf-8")
    result["notes"] = str(notes)
    return result
