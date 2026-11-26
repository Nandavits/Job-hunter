"""Replies to your applications: read your inbox (read-only) and update each job's status.

Rejection  -> status 'rejected'
Interview / assessment invitation -> status 'interview' (the interview prep pack is then made automatically)
Receipt confirmation / 'please complete your application' -> only a note

Only emails that clearly belong to one company you applied to are used: the company name must be in
the sender, the subject, or (for emails from application systems such as Greenhouse or Breezy) the text.
Nothing in your mailbox is changed, and nothing is ever sent.
"""
from __future__ import annotations

import email
import imaplib
import json
import logging
import re
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path

from .dedupe import normalize_company

log = logging.getLogger("jobhunter")

# "Unfortunately"/"leider" alone also appear in invitations ("unfortunately I am out on Friday"), so they
# only count when the email is not an invitation.
WEAK_REJECTION = re.compile(r"unfortunately|leider", re.I)
REJECTION = re.compile(
    r"regret to inform|not (?:be )?(?:moving|move|proceed(?:ing)?) forward|"
    r"(?:will|can) not (?:be )?proceed(?:ing)? with your application|not to (?:move|proceed) forward|"
    r"decided to (?:move forward|proceed|go ahead|continue) with other|more closely match(?:ed)? (?:by|with) other|"
    r"(?:other|another) candidates? (?:whose|who)|isn.t a (?:fit|match) at this time|not (?:a|the right) (?:fit|match)|"
    r"position has (?:already )?been filled|no longer (?:considering|available)|cannot offer you|"
    r"absage|nicht (?:weiter )?berücksichtig|anderen? (?:kandidat|bewerber)|"
    r"(?:können|werden) (?:ihnen|dir|ihre bewerbung) (?:leider )?(?:keine|nicht)", re.I)
INTERVIEW = re.compile(
    r"invit(?:e|ing) you (?:to|for)|(?:you(?:'ve| have)? been|you're|you are) invited|invited to (?:complete|take|join|an? |the |your )|"
    r"complete (?:an?|your|the) (?:evaluation|assessment|test|challenge|questionnaire)|start (?:the |your )?evaluation|invitation (?:to|for) (?:an? )?(?:interview|call|chat|assessment|evaluation)|"
    r"interview invitation|schedule (?:an? |your )?(?:interview|call|time|chat)|book (?:a|your) (?:time|slot|call)|"
    r"calendly\.com|cal\.com/|(?:first|next|technical|video|phone) (?:interview|call|round)|"
    r"(?:coding|technical|online) (?:assessment|challenge|test)|take[- ]home|case study|evaluation invite|"
    r"einladung zu|vorstellungsgespräch|kennenlerngespräch|kennenlernen möchten|nächste(?:n)? (?:schritt|runde)|"
    r"termin (?:vereinbaren|vorschlagen)", re.I)
RECEIVED = re.compile(
    r"thank(?:s| you) for (?:your |submitting your )?(?:application|applying)|confirmation that we received|(?:we have |we've )?received your application|"
    r"application (?:received|confirmation)|landed in our pipeline|vielen dank für (?:ihre|deine) bewerbung|"
    r"eingangsbestätigung|bewerbung (?:ist )?(?:bei uns )?eingegangen", re.I)
INCOMPLETE = re.compile(
    r"vervollständigen sie ihre bewerbung|schicken sie ihre bewerbung|complete your application|"
    r"finish your application|your application is (?:not|in)complete", re.I)

# Emails from application systems name the company in the text rather than the sender.
ATS_SENDERS = re.compile(r"greenhouse|lever\.co|ashbyhq|smartrecruiters|personio|breezy|workday|myworkday|"
                         r"join\.com|recruitee|softgarden|successfactors|teamtailor|workable|jobvite|"
                         r"icims|bamboohr|taleo|tendent|hirehive|dvinci|rexx|onlyfy|prescreen", re.I)
# Job boards and newsletters: their emails are about new jobs, never about your application.
NOT_A_REPLY = re.compile(r"jobalert|job-alert|jobagent|jobmail|jobs-noreply@linkedin|linkedin\.com|stepstone|indeed|"
                         r"xing\.com|glassdoor|jobvector|kimeta|yourfirm|jobware|goodjobs|interamt|joblift|"
                         r"arbeitnow|empfehlungsbund|jobrapido|talentglade|nextraise|newsletter|crio\.in|connect\.in|"
                         r"familienunternehmen|entrepreneursclub", re.I)
GENERIC_WORDS = {"data", "the", "group", "jobs", "digital", "tech", "solutions", "services", "global", "and",
                 "consulting", "company", "systems", "software", "labs", "formerly"}
STEP = {"applied": 0, "interview": 1, "rejected": 2, "offer": 3}


# "we would gladly invite you to become part of our pool of experts" is not an interview (Constaff)
NOT_AN_INVITATION = re.compile(r"[^.!?\n]*(?:(?:talent|expert|candidate|bewerber|kandidaten)[- ]?pool|pool of "
                               r"(?:experts|talents?|candidates)|feedback portal|newsletter)[^.!?\n]*[.!?]?", re.I)


# "If you don't hear from us within 4 weeks, ... nicht weiter berücksichtigt" is not a rejection (GENO Broker)
IF_SENTENCE = re.compile(r"(?:^|(?<=[.!?\n]))\s*(?:sollte[n]?|falls|wenn|if|should)\b[^.!?\n]*[.!?]?", re.I)
# "Im nächsten Schritt werden wir Ihre Unterlagen prüfen" is a receipt, not an interview (GOLDBECK)
REVIEW_STEP = re.compile(r"[^.!?\n]*(?:nächsten? schritt|next steps?)[^.!?\n]*(?:prüfen|sichten|review|melden|"
                         r"zukommen|get back|contact you|reach out|look at)[^.!?\n]*[.!?]?", re.I)


def classify(text: str) -> str:
    """'rejected', 'interview', 'received', 'incomplete' or ''. A rejection wins over everything else."""
    text = IF_SENTENCE.sub(" ", text)
    if REJECTION.search(text):
        return "rejected"
    if INTERVIEW.search(REVIEW_STEP.sub(" ", NOT_AN_INVITATION.sub(" ", text))):
        return "interview"
    # a receipt confirmation often explains what happens to those who "unfortunately" don't go on (Sandvik)
    if WEAK_REJECTION.search(text) and re.search(r"application|bewerbung|position|stelle|role", text, re.I) \
            and not RECEIVED.search(text):
        return "rejected"
    if INCOMPLETE.search(text):
        return "incomplete"
    if RECEIVED.search(text):
        return "received"
    return ""


def company_keys(company: str) -> list[str]:
    """'Almetra (formerly Deltia)' -> ['almetra', 'deltia'];  'Trade Republic' -> ['trade republic']."""
    raw = (company or "").lower()
    names = [normalize_company(raw)] + [normalize_company(x) for x in re.findall(r"\(([^)]*)\)", raw)]
    keys = []
    for name in names:
        name = re.sub(r"^formerly\s+|\s+formerly$", "", name).strip()
        if len(name) >= 3:
            keys.append(name)
        first = name.split()[0] if name else ""
        if len(first) >= 5 and first not in GENERIC_WORDS and first not in keys:
            keys.append(first)
    return [k for k in dict.fromkeys(keys) if k not in GENERIC_WORDS]


def _mentions(text: str, key: str) -> bool:
    text = text.lower()
    if len(key) >= 5:                         # 'getmoss.com', 'ablefy.breezy-mail.com' contain the name
        return key in text or key.replace(" ", "") in text
    return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text) is not None or \
        re.search(rf"(?:@|\.|get|join|team){re.escape(key)}\b", text) is not None


def match_job(sender: str, subject: str, body: str, jobs: list[dict]) -> dict | None:
    """The one applied job this email is about, or None if unclear."""
    head = f"{sender} {subject}"
    hits = [j for j in jobs if any(_mentions(head, k) for k in company_keys(j["company"]))]
    if not hits and ATS_SENDERS.search(sender):
        text = body[:3000]
        hits = [j for j in jobs if any(_mentions(text, k) for k in company_keys(j["company"]))]
    companies = {normalize_company(j["company"]) for j in hits}
    if len(companies) != 1:
        return None
    if len(hits) > 1:                         # several roles at one company: pick the one named in the email
        named = [j for j in hits if (j.get("title") or "").lower()[:25] in f"{subject} {body}".lower()]
        hits = named or sorted(hits, key=lambda j: j.get("applied_at") or "", reverse=True)
    return hits[0]


def apply_reply(store, job: dict, kind: str, subject: str, day: str) -> str:
    """Update the job for one reply. Returns what changed ('' if nothing)."""
    note = {"rejected": "rejection", "interview": "INTERVIEW invitation", "received": "application received",
            "incomplete": "the company says your application is NOT complete - check it"}[kind]
    line = f"[{day}] {note}: {subject[:80]}"
    notes = job.get("notes") or ""
    if line in notes:
        return ""
    values = {"notes": (notes + "\n" + line).strip()}
    status = job["status"]
    if kind in ("rejected", "interview") and STEP.get(kind, 0) > STEP.get(status, 0) and status != "offer":
        values["status"] = kind
    elif kind == "interview" and status == "rejected":
        # invited after the "rejection": that email was misread (e.g. a receipt that explains rejections)
        rejected_on = re.findall(r"\[(\d{4}-\d{2}-\d{2})\] rejection:", notes)
        if rejected_on and day >= max(rejected_on):
            values["status"] = "interview"
    store.update(job["uid"], **values)
    job.update(values)
    return values.get("status", "note")


def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _text(msg) -> str:
    plain, html = "", ""
    for part in msg.walk():
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        payload = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
        if kind == "text/plain" and not plain:
            plain = payload
        elif kind == "text/html" and not html:
            html = payload
    if plain:
        return plain
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script).*?</\1>", " ", html)))


RULES_VERSION = 4          # raise when the rules above change: emails read before are then read again


def _load_seen(path: Path) -> set:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if isinstance(data, dict) and data.get("version") == RULES_VERSION:
        return set(data.get("ids") or [])
    return set()


def check_replies(store, address: str, app_password: str, cfg: dict, seen_file: Path,
                  skip_senders: list[str] = ()) -> list[str]:
    """Read recent emails and update applied jobs. Returns one line per change."""
    if not (address and app_password) or not cfg.get("enabled", True):
        return []
    jobs = [j for j in store.list(limit=100_000) if j["status"] in ("applied", "interview")]
    if not jobs:
        return []
    seen = _load_seen(seen_file)
    since = (datetime.now() - timedelta(days=int(cfg.get("days_back", 14)))).strftime("%d-%b-%Y")
    skip = [s.lower() for s in skip_senders] + [address.lower()]
    changes: list[str] = []
    with imaplib.IMAP4_SSL(cfg.get("imap_host", "imap.gmail.com")) as imap:
        imap.login(address, app_password)
        imap.select(cfg.get("folder", "INBOX"), readonly=True)          # read-only: nothing is changed
        status, data = imap.search(None, "SINCE", since)
        nums = data[0].split() if status == "OK" else []
        heads = {}
        if nums:                                                          # all headers in one request
            _, fetched = imap.fetch(b",".join(nums), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
            for item in fetched:
                if isinstance(item, tuple):
                    heads[item[0].split()[0]] = email.message_from_bytes(item[1])
        for num in nums:
            head = heads.get(num)
            if head is None:
                continue
            msg_id = (head.get("Message-ID") or "").strip()
            sender = _decode(head.get("From", ""))
            if msg_id in seen or any(s in sender.lower() for s in skip) or NOT_A_REPLY.search(sender):
                continue
            subject = _decode(head.get("Subject", ""))
            _, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            body = _text(msg)
            kind = classify(f"{subject}\n{body[:4000]}")
            if not kind:
                seen.add(msg_id)                       # not about an application: never read again
                continue
            job = match_job(f"{sender} {parseaddr(sender)[1]}", subject, body, jobs)
            if not job:
                continue                               # maybe a job you mark 'applied' later: look again next time
            seen.add(msg_id)
            try:
                day = email.utils.parsedate_to_datetime(head.get("Date")).date().isoformat()
            except (TypeError, ValueError):
                day = date.today().isoformat()
            changed = apply_reply(store, job, kind, subject, day)
            if changed:
                changes.append(f"{job['company']} - {job['title']}: {changed} ({subject[:60]})")
    try:
        seen_file.write_text(json.dumps({"version": RULES_VERSION, "ids": sorted(seen)[-5000:]}), encoding="utf-8")
    except OSError:
        pass
    for line in changes:
        log.info("Inbox: %s", line)
    return changes
