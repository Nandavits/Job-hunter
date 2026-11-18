"""Tailor the CV and write a cover letter for one job.

With ANTHROPIC_API_KEY set, Claude picks the CV title, rewrites the summary, orders bullets and
skills, and writes the cover letter — using ONLY facts from profile.yaml. Without a key a
rule-based fallback produces a decent (more generic) version, so the pipeline always works.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .style import STYLE_RULES, problems, tidy

log = logging.getLogger("jobhunter")

# Tools that often appear in data job ads; used to name honest gaps in the fallback letter.
KNOWN_TOOLS = {"kubernetes": "Kubernetes", "bigquery": "BigQuery", "snowflake": "Snowflake",
               "databricks": "Databricks", "spark": "Spark", "kafka": "Kafka", "terraform": "Terraform",
               "aws": "AWS", "azure": "Azure", "gcp": "GCP", "google cloud": "Google Cloud",
               "dagster": "Dagster", "looker": "Looker", "fivetran": "Fivetran", "redshift": "Redshift",
               "scala": "Scala", "java": "Java", "flink": "Flink", "airbyte": "Airbyte",
               "posthog": "PostHog", "sqlmesh": "SQLMesh"}


def _skill_variants(skill: str) -> list[str]:
    """'Apache Airflow' -> ['apache airflow', 'airflow']; 'PostgreSQL' -> ['postgresql', 'postgres']."""
    base = skill.split(" (")[0].strip().lower()
    variants = {base}
    if base.startswith("apache "):
        variants.add(base[7:])
    if base == "postgresql":
        variants.add("postgres")
    if base.startswith("etl/elt"):
        variants.update({"etl", "elt"})
    return list(variants)


def _skill_in(text: str, skill: str) -> bool:
    return any(_has(text, v) for v in _skill_variants(skill))


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


@dataclass
class Tailoring:
    title: str
    summary: str
    bullet_order: dict[int, list[int]]
    skills_order: list[str]
    letter_paragraphs: list[str]
    fit_notes: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    engine: str = "fallback"
    skill_priority: list[str] = field(default_factory=list)   # your skills this ad asks for, listed first


def skills_in_ad(profile: dict, job_text: str) -> list[str]:
    """Every skill from your profile that the job ad mentions (exact profile wording)."""
    job_text = job_text.lower()
    return [s for cat in profile.get("skills", {}).values() for s in cat if _skill_in(job_text, s)]


# --------------------------------------------------------------------------- helpers
def _profile_text(profile: dict) -> str:
    return json.dumps(profile, ensure_ascii=False).lower()


def _has(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(word.lower())}(?![\w])", text) is not None


def _pick_title(job_title: str, default: str) -> str:
    t = job_title.lower()
    if "analytics engineer" in t:
        return "Analytics Engineer"
    if "data engineer" in t or "data platform" in t or "dataops" in t or "etl" in t:
        return "Data Engineer"
    if "automation" in t and ("bi" in t.split() or "business intelligence" in t):
        return "BI & Data Engineer"
    if "bi " in t or t.startswith("bi") or "business intelligence" in t:
        return "BI & Data Engineer"
    if "analyst" in t:
        return "Data Analyst"
    return default


# Words that appear in almost every data job ad and say nothing about fit.
COMMON_WORDS = set("""data with from that this team teams business requirements models model stakeholders
work working build built using used across into their your other within reporting analysis production
platform early flag translate partner maintain develop""".split())


def _bullet_scores(bullets: list[dict], job_text: str) -> list[int]:
    """Tags (tools/skills) count 3x; ordinary words count 1x; common words not at all."""
    scores = []
    for b in bullets:
        tags = {t.lower() for t in b.get("tags", [])}
        words = {w.lower() for w in re.findall(r"[A-Za-z][\w+.-]{3,}", b["text"])} - COMMON_WORDS - tags
        scores.append(3 * sum(_has(job_text, t) for t in tags) + sum(_has(job_text, w) for w in words))
    return scores


def gaps_for(job_text: str, profile: dict) -> list[str]:
    ptext = _profile_text(profile)
    return [name for key, name in KNOWN_TOOLS.items() if _has(job_text, key) and not _has(ptext, key)]


# --------------------------------------------------------------------------- fallback
def fallback(profile: dict, job: dict) -> Tailoring:
    job_text = f"{job.get('title', '')}\n{job.get('description', '')}".lower()
    order = {}
    for i, exp in enumerate(profile.get("experience", [])):
        scores = _bullet_scores(exp["bullets"], job_text)
        order[i] = sorted(range(len(exp["bullets"])), key=lambda k: (-scores[k], k))
        if i == 0 and order[i] and 0 in order[i]:  # keep the "ownership" bullet first
            order[i].remove(0)
            order[i].insert(0, 0)

    skills = profile.get("skills", {})
    def cat_score(cat):
        return sum(_skill_in(job_text, s) for s in skills[cat])
    skills_order = sorted(skills, key=lambda c: (-cat_score(c), list(skills).index(c)))

    matched = [s.split(" (")[0] for cat in skills.values() for s in cat if _skill_in(job_text, s)]
    matched = list(dict.fromkeys(m.replace("Apache ", "") for m in matched))[:5] or ["SQL", "dbt", "Airflow"]
    gaps = gaps_for(job_text, profile)

    exp0 = profile["experience"][0]
    top = [exp0["bullets"][k]["text"] for k in order[0][:3]]
    company = job.get("company") or "your company"
    title = job.get("title") or "this"
    city = profile.get("personal", {}).get("city", "Berlin")

    def sentence(text: str) -> str:
        return text[0].lower() + text[1:] if text else text

    paragraphs = [
        f"I am applying for the {title} position at {company}. The role's focus on "
        f"{_join(matched)} is the work I do every day at my current job.",
        f"In my role as {exp0['title']} at {exp0['company']}, I {sentence(top[0]).rstrip('.')}. "
        f"In this role I {sentence(top[1]).rstrip('.')}." + (
            f" I also {sentence(top[2]).rstrip('.')}." if len(top) > 2 else ""),
        "I work hands-on with " + _join(matched) + ". I check data against its sources before it "
        "reaches stakeholders." + (
            f" {_join(gaps[:3])} would be new to me, "
            "but the concepts carry over directly from my current work and I pick up new tools quickly."
            if gaps else ""),
        f"I am based in {city}{', ' + _lower_first(profile['relocation'].rstrip('.')) + ',' if profile.get('relocation') else ''} "
        f"and work fully in English. I would be glad to talk about the role in an interview.",
    ]
    return Tailoring(
        title=_pick_title(job.get("title", ""), profile.get("default_title", "")),
        summary=profile.get("default_summary", "").strip(),
        bullet_order=order,
        skills_order=skills_order,
        letter_paragraphs=paragraphs,
        fit_notes=[f"Matched skills: {', '.join(matched)}"],
        gaps=gaps,
        skill_priority=skills_in_ad(profile, job_text),
    )


# --------------------------------------------------------------------------- Claude
SYSTEM_PROMPT = """You tailor a candidate's CV and cover letter to one job ad.

Hard rules:
- Use ONLY facts in the candidate profile. Never invent tools, employers, numbers, degrees or results.
- You may reorder and rephrase. You may say a tool from the job ad is new to the candidate, honestly.
- Name every tool the ad asks for that the candidate really has in the summary or the letter, using
  the ad's own wording (e.g. "Postgres" if the ad says Postgres), so recruiters and ATS filters find it.
  Never list a tool the candidate does not have as a skill.
- CV title: short (2-4 words), close to the job title, and truthful for the candidate's experience.
- Summary: 55-85 words, third-person-free CV style (no "I"), leading with what this job cares about most.
- Cover letter: exactly 4 paragraphs, 230-320 words total, in English, no greeting or sign-off lines.
  Paragraph 1: why this role/company (specific to the ad). Paragraph 2: strongest matching evidence
  from the profile. Paragraph 3: further fit + honest note on the main gap, if any. Paragraph 4: based
  in the candidate's city, works in English, wants to discuss.
- bullet_order: for each experience index, the bullet indices from most to least relevant. Include every index.
- skills_order: every skills category name, most relevant first.

""" + STYLE_RULES + """

Return the result by calling the save_tailoring tool."""

# A tool schema makes Claude return well-formed JSON (no broken quotes inside the letter).
TAILORING_TOOL = {
    "name": "save_tailoring",
    "description": "Save the tailored CV settings and cover letter.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "bullet_order": {"type": "object", "description": 'experience index -> bullet indices, e.g. {"0": [2,0,1]}',
                             "additionalProperties": {"type": "array", "items": {"type": "integer"}}},
            "skills_order": {"type": "array", "items": {"type": "string"}},
            "cover_letter": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4},
            "fit_notes": {"type": "array", "items": {"type": "string"}},
            "gaps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "summary", "bullet_order", "skills_order", "cover_letter", "fit_notes", "gaps"],
    },
}


def _profile_for_prompt(profile: dict) -> dict:
    p = {k: v for k, v in profile.items() if k not in ("personal", "answers")}
    p["city"] = profile.get("personal", {}).get("city", "")
    p["relocation"] = profile.get("relocation", "")
    p["experience"] = [
        {**{k: v for k, v in e.items() if k != "bullets"},
         "bullets": {str(i): b["text"] for i, b in enumerate(e["bullets"])}}
        for e in profile.get("experience", [])
    ]
    return p


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def _validate(data: dict, profile: dict, base: Tailoring) -> Tailoring:
    order = {}
    for i, exp in enumerate(profile.get("experience", [])):
        n = len(exp["bullets"])
        given = [int(x) for x in (data.get("bullet_order") or {}).get(str(i), []) if str(x).isdigit()]
        given = [x for x in dict.fromkeys(given) if 0 <= x < n]
        order[i] = given + [x for x in range(n) if x not in given]
    cats = list(profile.get("skills", {}))
    skills = [c for c in data.get("skills_order", []) if c in cats]
    skills += [c for c in cats if c not in skills]
    letter = [p.strip() for p in data.get("cover_letter", []) if isinstance(p, str) and p.strip()]
    return Tailoring(
        title=(data.get("title") or base.title).strip()[:60],
        summary=(data.get("summary") or base.summary).strip(),
        bullet_order=order,
        skills_order=skills,
        letter_paragraphs=letter if len(letter) >= 3 else base.letter_paragraphs,
        fit_notes=[str(x) for x in data.get("fit_notes", [])][:6],
        gaps=[str(x) for x in data.get("gaps", [])][:6],
        engine="claude",
        skill_priority=base.skill_priority,
    )


def tailor(profile: dict, job: dict, api_key: str = "", model: str = "claude-sonnet-5-5",
           client=None) -> Tailoring:
    base = fallback(profile, job)
    if not (api_key or client):
        return base
    try:
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
        ad = f"Company: {job.get('company')}\nTitle: {job.get('title')}\nLocation: {job.get('location')}\n\n" \
             f"{(job.get('description') or '')[:9000]}"
        messages = [{"role": "user", "content":
                     f"<candidate_profile>\n{json.dumps(_profile_for_prompt(profile), ensure_ascii=False, indent=1)}"
                     f"\n</candidate_profile>\n\n<job_ad>\n{ad}\n</job_ad>"}]
        result = None
        for attempt in range(3):             # retry if no result came back, or AI phrases slipped in
            msg = client.messages.create(model=model, max_tokens=2500, system=SYSTEM_PROMPT,
                                         tools=[TAILORING_TOOL],
                                         tool_choice={"type": "auto"},   # forcing a tool is not supported by every model
                                         messages=messages)
            data = next((dict(b.input) for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
            if data is None:
                try:
                    data = _extract_json("".join(getattr(b, "text", "") for b in msg.content))
                except ValueError:
                    if attempt == 2:
                        raise
                    messages = messages + [{"role": "assistant", "content": msg.content},
                                           {"role": "user", "content": "Call the save_tailoring tool with the result now."}]
                    continue
            result = _validate(data, profile, base)
            found = problems(" ".join([result.summary, *result.letter_paragraphs]))
            if not found or attempt == 2:
                break
            log.info("Rewriting letter for %s without: %s", job.get("company"), ", ".join(found))
            messages = messages + [
                {"role": "assistant", "content": msg.content},
                *([{"role": "user", "content": [{"type": "tool_result", "tool_use_id": b.id,
                                                 "content": "Rejected: banned phrases " + ", ".join(found) +
                                                 ". Rewrite everything plainly with facts only and call the tool again."}
                                                for b in msg.content if getattr(b, "type", "") == "tool_use"]}]
                  if any(getattr(b, "type", "") == "tool_use" for b in msg.content) else
                  [{"role": "user", "content": "Rewrite without these phrases: " + ", ".join(found)}]),
            ]
        result.summary = tidy(result.summary)
        result.letter_paragraphs = [tidy(p) for p in result.letter_paragraphs]
        return result
    except Exception as exc:
        log.warning("AI tailoring failed (%s); using rule-based version", exc)
        return base
