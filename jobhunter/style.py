"""Writing style for letters and form answers: plain, short, factual, no typical AI phrasing."""
from __future__ import annotations

import re

STYLE_RULES = """Writing style (very important):
- Write like a real junior data engineer writing their own letter: plain English, short sentences
  (mostly under 20 words), one idea per sentence. Simple words. No marketing tone.
- Every claim must be a concrete fact from the profile: a tool, a task, a number, a result.
  Say "I built dbt models on PostgreSQL for Superset dashboards", not "I have strong expertise in data".
- Do not use these words or phrases: excited, thrilled, passionate, passion, eager, delighted, leverage,
  utilize, spearhead, synergy, dynamic, fast-paced, thrive, cutting-edge, innovative, seamless, robust,
  holistic, journey, landscape, ever-evolving, realm, tapestry, delve, embark, foster, empower, unlock,
  game-changer, world-class, "I am writing to", "I am confident that", "I believe I would be a great fit",
  "perfect fit", "ideal candidate", "unique blend", "proven track record", "track record",
  "strong background", "align(s) with my", "resonates with me", "I would welcome the opportunity",
  "look forward to the opportunity", "make a meaningful impact", "drive impact", "valuable asset",
  "hit the ground running", "wear many hats", "deeply", "truly", "incredibly".
- No dashes as punctuation (no "—" or "–"); use a full stop or a comma instead.
- No rhetorical questions, no exclamation marks, no lists of three adjectives."""

BANNED = re.compile(
    r"\b(excited|thrilled|passionate|passion|eager|delighted|leverag\w*|utili[sz]\w*|spearhead\w*|synerg\w*|"
    r"dynamic|fast-paced|thrive\w*|cutting-edge|innovative|seamless\w*|robust|holistic|journey|landscape|"
    r"ever-evolving|realm|tapestry|delv\w*|embark\w*|foster\w*|empower\w*|unlock\w*|game-changer|world-class|"
    r"perfect fit|ideal candidate|unique blend|track record|strong background|resonates? with|valuable asset|"
    r"meaningful impact|drive impact|hit the ground running|wear many hats|deeply|truly|incredibly)\b|"
    r"I am writing to|I am confident that|great fit|aligns? (?:perfectly |well )?with my|"
    r"welcome the opportunity|look forward to the opportunity", re.I)


def tidy(text: str) -> str:
    """Remove dash punctuation and exclamation marks that make text read as machine-written."""
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r"\s+-\s+", ", ", text)
    text = text.replace("!", ".")
    return re.sub(r",\s*,", ",", text).strip()


def problems(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in BANNED.finditer(text)})
