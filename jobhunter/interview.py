"""Interview preparation pack for one job: what they care about, likely questions with answers built
only from your profile, honest answers for gaps, questions to ask them, and a 60-second pitch.

Uses the Claude API when a key is set; otherwise a rule-based pack from the ad and your profile.
Written as Interview_Prep_<Company>.docx next to the CV and letter for that job.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from .style import problems, tidy
from .tailor import _extract_json, _profile_for_prompt, gaps_for, skills_in_ad

log = logging.getLogger("jobhunter")

SYSTEM_PROMPT = """You prepare a candidate for a job interview in Germany.
Use ONLY facts from <candidate_profile>. Never invent employers, projects, numbers, tools or results.
If the ad asks for something the candidate has not done, say so honestly in "gaps" and show the
closest real experience and how they would learn it; never pretend.
Write answers in the first person, as short talking points the candidate can say out loud
(situation, what I did, result), plain English, no buzzwords.
Return the result by calling the save_interview_prep tool."""

PREP_TOOL = {
    "name": "save_interview_prep",
    "description": "Save the interview preparation pack.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pitch": {"type": "string", "description": "60-second 'tell me about yourself' answer for this role"},
            "what_they_want": {"type": "array", "items": {"type": "string"},
                               "description": "3-6 things this employer clearly cares about, from the ad"},
            "questions": {"type": "array", "minItems": 8, "maxItems": 14, "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "kind": {"type": "string", "enum": ["technical", "behavioural", "role", "practical"]},
                    "answer_points": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "kind", "answer_points"]}},
            "gaps": {"type": "array", "items": {
                "type": "object",
                "properties": {"gap": {"type": "string"}, "honest_answer": {"type": "string"}},
                "required": ["gap", "honest_answer"]}},
            "ask_them": {"type": "array", "minItems": 4, "maxItems": 7, "items": {"type": "string"}},
            "study_list": {"type": "array", "items": {"type": "string"},
                           "description": "Concrete topics to revise before the interview"},
        },
        "required": ["pitch", "what_they_want", "questions", "gaps", "ask_them", "study_list"],
    },
}

ASK_THEM = [
    "What would a successful first six months look like in this role?",
    "How is the data team organised, and who would I work with most closely?",
    "What does the current data stack look like, and what would you like to change about it?",
    "How do you make sure the numbers in your dashboards can be trusted?",
    "What are the next steps in the hiring process?",
]

BEHAVIOURAL = [
    ("Tell me about a data-quality problem you found and fixed.", ("data quality", "debugging", "reconciliation")),
    ("Tell me about a time you worked with a non-technical stakeholder.", ("stakeholders", "communication")),
    ("Tell me about something you automated.", ("automation", "n8n", "orchestration")),
    ("Tell me about a time something was slow or broken and you improved it.", ("performance", "monitoring")),
]


def _bullets(profile: dict) -> list[tuple[str, list[str]]]:
    return [(b["text"], [t.lower() for t in b.get("tags", [])])
            for e in profile.get("experience", []) for b in e.get("bullets", [])]


def _points_for(profile: dict, words: tuple[str, ...], limit: int = 2) -> list[str]:
    hits = [text for text, tags in _bullets(profile)
            if any(w in tags or w in text.lower() for w in words)]
    return hits[:limit] or [text for text, _ in _bullets(profile)[:1]]


def fallback(profile: dict, job: dict) -> dict:
    ad = f"{job.get('title', '')}\n{job.get('description') or ''}".lower()
    have = list(dict.fromkeys(re.sub(r"^apache ", "", s.split(" (")[0], flags=re.I)
                              for s in skills_in_ad(profile, ad)))
    missing = gaps_for(ad, profile)
    company = job.get("company") or "the company"
    questions = [{"question": f"Walk me through how you have used {s}.", "kind": "technical",
                  "answer_points": _points_for(profile, (s.lower(),))} for s in have[:6]]
    questions += [{"question": q, "kind": "behavioural", "answer_points": _points_for(profile, words)}
                  for q, words in BEHAVIOURAL]
    questions += [
        {"question": f"Why do you want to work at {company}?", "kind": "role",
         "answer_points": ["Connect what the ad describes to the work you enjoy most, using one real example."]},
        {"question": "What is your German level, and are you willing to improve it?", "kind": "practical",
         "answer_points": [", ".join(profile.get("languages", [])) or "State your level honestly."]},
    ]
    return {
        "pitch": tidy(profile.get("default_summary", "")),
        "what_they_want": [f"Experience with {s}" for s in have[:5]] or [job.get("title", "")],
        "questions": questions,
        "gaps": [{"gap": g, "honest_answer": f"I have not used {g} in production yet. The closest thing I have "
                  f"done is listed in my experience; I would learn {g} by building a small project with it "
                  f"in my first weeks."} for g in missing],
        "ask_them": ASK_THEM,
        "study_list": [f"Revise {s}: be ready to explain one real example in detail" for s in have[:5]]
                      + [f"Basics of {g}" for g in missing],
        "engine": "fallback",
    }


def _clean(data: dict, base: dict) -> dict:
    out = dict(base)
    for key in ("pitch",):
        if isinstance(data.get(key), str) and data[key].strip():
            out[key] = tidy(data[key])
    for key in ("what_they_want", "ask_them", "study_list"):
        items = [tidy(str(x)) for x in data.get(key) or [] if str(x).strip()]
        if items:
            out[key] = items
    qs = [{"question": str(q.get("question", "")).strip(), "kind": q.get("kind", "technical"),
           "answer_points": [tidy(str(p)) for p in q.get("answer_points") or [] if str(p).strip()]}
          for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if len(qs) >= 5:
        out["questions"] = qs
    gaps = [{"gap": str(g.get("gap", "")).strip(), "honest_answer": tidy(str(g.get("honest_answer", "")))}
            for g in data.get("gaps") or [] if isinstance(g, dict) and g.get("gap")]
    if gaps or isinstance(data.get("gaps"), list):
        out["gaps"] = gaps
    out["engine"] = "claude"
    return out


def build_prep(profile: dict, job: dict, api_key: str = "", model: str = "claude-sonnet-5-5",
               client=None) -> dict:
    base = fallback(profile, job)
    if not (api_key or client):
        return base
    try:
        if client is None:
            from .llm import make_client
            client = make_client(api_key)
        ad = (f"Company: {job.get('company')}\nTitle: {job.get('title')}\nLocation: {job.get('location')}\n\n"
              f"{(job.get('description') or '')[:9000]}")
        msg = client.messages.create(
            model=model, max_tokens=4000, system=SYSTEM_PROMPT, tools=[PREP_TOOL], tool_choice={"type": "auto"},
            messages=[{"role": "user", "content":
                       f"<candidate_profile>\n{json.dumps(_profile_for_prompt(profile), ensure_ascii=False, indent=1)}"
                       f"\n</candidate_profile>\n\n<job_ad>\n{ad}\n</job_ad>"}])
        data = next((dict(b.input) for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if data is None:
            data = _extract_json("".join(getattr(b, "text", "") for b in msg.content))
        result = _clean(data, base)
        found = problems(result["pitch"])
        if found:
            log.info("Interview pitch for %s used phrases %s; tidied", job.get("company"), ", ".join(found))
        return result
    except Exception as exc:
        log.warning("AI interview prep failed (%s); using rule-based version", exc)
        return base


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")[:40] or "Company"


def folder_for(job: dict, out_root: Path) -> Path:
    if job.get("cv_path"):
        return Path(job["cv_path"]).parent
    return out_root / f"{date.today().isoformat()}_{_slug(job.get('company', ''))}_{_slug(job.get('title', ''))}"


def prep_path(job: dict, out_root: Path) -> Path:
    return folder_for(job, out_root) / f"Interview_Prep_{_slug(job.get('company', ''))}.docx"


def write_prep(prep: dict, job: dict, path: Path) -> Path:
    from docx import Document
    from docx.shared import Pt

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    doc.add_heading(f"Interview preparation: {job.get('company', '')}", level=0)
    doc.add_paragraph(f"{job.get('title', '')} · {job.get('location', '')}".strip(" ·"))
    if job.get("application_url") or job.get("url"):
        doc.add_paragraph(f"Job ad: {job.get('application_url') or job.get('url')}")

    doc.add_heading("Your 60-second introduction", level=1)
    doc.add_paragraph(prep["pitch"])

    doc.add_heading("What they care about", level=1)
    for item in prep["what_they_want"]:
        doc.add_paragraph(item, style="List Bullet")

    doc.add_heading("Likely questions and your answers", level=1)
    labels = {"technical": "Technical", "behavioural": "Behavioural", "role": "About the role",
              "practical": "Practical"}
    for i, q in enumerate(prep["questions"], 1):
        doc.add_heading(f"{i}. {q['question']}", level=2)
        doc.add_paragraph(labels.get(q.get("kind"), "Question")).runs[0].italic = True
        for point in q["answer_points"]:
            doc.add_paragraph(point, style="List Bullet")

    doc.add_heading("Gaps: answer honestly", level=1)
    if prep["gaps"]:
        for g in prep["gaps"]:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{g['gap']}: ").bold = True
            p.add_run(g["honest_answer"])
    else:
        doc.add_paragraph("No clear gaps against this ad.")

    doc.add_heading("Questions to ask them", level=1)
    for q in prep["ask_them"]:
        doc.add_paragraph(q, style="List Number")

    doc.add_heading("Revise before the interview", level=1)
    for s in prep["study_list"]:
        doc.add_paragraph(s, style="List Bullet")

    note = "Written with Claude from your profile and the job ad." if prep.get("engine") == "claude" else \
        "Rule-based version (no Claude API key): add your own details to each answer."
    doc.add_paragraph(note).runs[0].italic = True
    doc.save(path)
    return path


def prepare_interview(settings, store, job: dict, out_root: Path, force: bool = False) -> Path:
    """Create the pack for a job once (or again with force=True). Returns the file path."""
    path = prep_path(job, out_root)
    if path.exists() and not force:
        return path
    prep = build_prep(settings.profile, job, api_key=settings.env("ANTHROPIC_API_KEY"),
                      model=settings.env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    write_prep(prep, job, path)
    note = f"{date.today():%Y-%m-%d}: interview prep written ({path.name})"
    if note not in (job.get("notes") or ""):
        store.update(job["uid"], notes=((job.get("notes") or "") + "\n" + note).strip())
    log.info("Interview prep for %s – %s (%s)", job.get("company"), job.get("title"), prep.get("engine"))
    return path


def prepare_due(settings, store, out_root: Path, limit: int = 3) -> list[Path]:
    """Every job you moved to 'interview' gets its pack automatically on the next run."""
    done = []
    for job in store.list(status="interview", limit=1000):
        if len(done) >= limit:
            break
        if not prep_path(job, out_root).exists():
            try:
                done.append(prepare_interview(settings, store, job, out_root))
            except Exception as exc:
                log.warning("Interview prep for %s failed: %s", job.get("company"), exc)
    return done
