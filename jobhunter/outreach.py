"""LinkedIn outreach drafts: a short connection note and a follow-up message for one job.

Job Hunter only writes the text and a people-search link. You open LinkedIn and send it yourself:
nothing is ever sent or clicked on LinkedIn.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from urllib.parse import quote

from .style import STYLE_RULES, problems, tidy

log = logging.getLogger("jobhunter")

NOTE_LIMIT = 200          # LinkedIn's limit for connection notes on free accounts


@dataclass
class Outreach:
    note: str             # connection request note, max 200 characters
    message: str          # longer message once connected (or InMail)
    search_url: str       # LinkedIn people search for the right person
    engine: str = "template"


def people_search_url(company: str, title: str) -> str:
    """LinkedIn people search: data team leads and recruiters at the company."""
    role = "Head of Data OR Data Engineering Lead OR Analytics Lead OR Recruiter"
    if re.search(r"analyst|bi\b|business intelligence", title, re.I):
        role = "Head of Analytics OR BI Lead OR Data Lead OR Recruiter"
    keywords = f"{company} {role}"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote(keywords)}"


def _short(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut if cut.endswith(".") else cut + "."


def template(profile: dict, job: dict, matched: list[str]) -> Outreach:
    me = profile.get("personal", {})
    exp0 = (profile.get("experience") or [{}])[0]
    names = [m.split(" (")[0].replace("Apache ", "") for m in matched[:3] if m]
    tools = (", ".join(names[:-1]) + " and " + names[-1]) if len(names) > 1 else (names[0] if names else "SQL, dbt and Airflow")
    company, title = job.get("company", "your company"), job.get("title", "the open role")
    note = _short(f"Hi, I applied for the {title} role at {company}. I build data pipelines with {tools} "
                  f"at {exp0.get('company', 'my current job')}. Happy to connect.", NOTE_LIMIT)
    message = (
        f"Hi, thanks for connecting. I applied for the {title} position at {company} this week.\n\n"
        f"At {exp0.get('company', 'my current job')} I work as {exp0.get('title', 'a data engineer')}. "
        f"I use {tools} every day, mostly for pipelines and data models that feed dashboards.\n\n"
        f"If you are part of the hiring team, I would be glad to answer any questions. "
        f"My CV is attached to the application.\n\n"
        f"Best regards,\n{me.get('first_name', '')} {me.get('last_name', '')}".strip()
    )
    return Outreach(note, message, people_search_url(company, title))


PROMPT = """Write LinkedIn outreach for a job applicant who has just applied. Use ONLY facts from the
candidate profile and the cover letter. Never invent experience, numbers or tools.

1. note: a connection request note, at most 200 characters, to a data team lead or recruiter at the
   company. Mention the exact role and one concrete fact that fits the job. No greeting by name
   (the name is unknown), start with "Hi,".
2. message: 60-110 words for after they accept. Say you applied, give 2 concrete facts that match the
   job ad, offer to answer questions. Sign with the candidate's first and last name.

""" + STYLE_RULES + """

Reply in exactly this plain-text format, nothing else:
NOTE: <the connection note>
MESSAGE:
<the message>"""


def draft(profile: dict, job: dict, letter_paragraphs: list[str], matched: list[str],
          api_key: str = "", model: str = "claude-sonnet-5-5", client=None) -> Outreach:
    base = template(profile, job, matched)
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not (key or client):
        return base
    try:
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
        facts = {k: profile.get(k) for k in ("personal", "experience", "skills", "projects")}
        facts["personal"] = {k: (facts.get("personal") or {}).get(k) for k in ("first_name", "last_name", "city")}
        msg = client.messages.create(model=model, max_tokens=600, system=PROMPT, messages=[{
            "role": "user", "content":
            f"Company: {job.get('company')}\nRole: {job.get('title')}\n"
            f"Job ad (excerpt): {(job.get('description') or '')[:2500]}\n\n"
            f"Cover letter: {' '.join(letter_paragraphs)}\n\nCandidate: {json.dumps(facts, ensure_ascii=False)[:6000]}"}])
        text = "".join(getattr(b, "text", "") for b in msg.content).strip()
        m = re.search(r"NOTE:\s*(.+?)\s*MESSAGE:\s*(.+)", text, re.S | re.I)
        if m:
            note, message = m.group(1), m.group(2)
        else:                                  # also accept JSON, in case the model answers that way
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
            note, message = str(data.get("note", "")), str(data.get("message", ""))
        note = tidy(note.strip().strip('"'))
        message = "\n".join(tidy(line) if line.strip() else "" for line in message.strip().strip('"').split("\n"))
        if not note or not message or problems(note + " " + message):
            return base
        return Outreach(_short(note, NOTE_LIMIT), message, base.search_url, engine="claude")
    except Exception as exc:
        log.warning("LinkedIn message draft failed (%s); using the template", str(exc)[:120])
        return base


def as_text(o: Outreach, job: dict) -> str:
    return (f"LinkedIn outreach for {job.get('company')} - {job.get('title')}\n"
            f"{'=' * 60}\n\n"
            f"1. Find the person (data team lead or recruiter):\n   {o.search_url}\n\n"
            f"2. Connection note ({len(o.note)}/{NOTE_LIMIT} characters):\n\n{o.note}\n\n"
            f"3. Message after they accept:\n\n{o.message}\n\n"
            f"Send it yourself on LinkedIn. Job Hunter never sends anything.\n")
