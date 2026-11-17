"""Daily digest: a short list of the best new jobs, sent by email and/or Telegram."""
from __future__ import annotations

import html
import logging
import mimetypes
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import requests

log = logging.getLogger("jobhunter")


def build_digest(new_jobs: list[dict], prepared: dict[str, dict], stats: dict,
                 reposts: list[dict] | None = None) -> tuple[str, str]:
    """Return (markdown, html) for the digest."""
    today = date.today().strftime("%A %d %B %Y")
    md = [f"# Job Hunter – {today}", "", f"**{len(new_jobs)} new matching jobs.** "
          f"Tracker: {stats.get('applied', 0)} applied · {stats.get('interview', 0)} interviews · "
          f"{stats.get('prepared', 0)} ready to send", ""]
    rows = []
    for i, job in enumerate(new_jobs, 1):
        docs = prepared.get(job["uid"])
        ready = "✅ CV + letter ready" if docs else ""
        review = " · ⚠ check description" if job.get("source") == "email" else ""
        md.append(f"{i}. **{job['title']}** – {job['company']} ({job.get('location') or 'n/a'}) · "
                  f"score {job['score']} {ready}{review}  \n   {job.get('apply_url') or job.get('url')}  \n"
                  f"   id `{job['uid'][:8]}`")
        rows.append(
            f"<tr><td>{i}</td><td><a href='{html.escape(job.get('apply_url') or job.get('url'))}'>"
            f"{html.escape(job['title'])}</a><br><small>{html.escape(job['company'])} · "
            f"{html.escape(job.get('location') or '')}</small></td><td>{job['score']}</td>"
            f"<td>{'✅' if docs else ''}</td><td><code>{job['uid'][:8]}</code></td></tr>")
    reposts = reposts or []
    if reposts:
        md += ["", f"Skipped {len(reposts)} repost(s) you already applied to:"]
        md += [f"- {r['title']} – {r['company']} (applied {r.get('applied_on') or 'earlier'})" for r in reposts]
    md += ["", "Next: `python -m jobhunter fill <id>` opens the form pre-filled. You click Submit."]
    page = (f"<h2>Job Hunter – {today}</h2><p><b>{len(new_jobs)} new matching jobs</b></p>"
            "<table cellpadding=6 border=1 style='border-collapse:collapse;font-family:sans-serif'>"
            "<tr><th>#</th><th>Job</th><th>Score</th><th>Docs</th><th>ID</th></tr>" + "".join(rows) +
            "</table>" + ("<p><b>Skipped reposts you already applied to:</b><br>" + "<br>".join(
                f"{html.escape(r['title'])} – {html.escape(r['company'])} (applied {html.escape(r.get('applied_on') or 'earlier')})"
                for r in reposts) + "</p>" if reposts else "") +
            "<p>Run <code>python -m jobhunter fill &lt;id&gt;</code> to open a pre-filled form. "
            "You always click Submit yourself.</p>")
    return "\n".join(md), page


def send_email(address: str, app_password: str, subject: str, text: str, html_body: str,
               attachments: list[str] | None = None) -> bool:
    if not (address and app_password):
        log.info("Email digest skipped: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set")
        return False
    msg = EmailMessage()
    msg["From"] = msg["To"] = address
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    for path in (attachments or [])[:10]:
        p = Path(path)
        if p.exists() and p.stat().st_size < 5_000_000:
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(address, app_password)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("Email digest failed: %s", exc)
        return False


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not (token and chat_id):
        log.info("Telegram digest skipped: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return False
    try:
        for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
            resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
                                 timeout=20)
            resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("Telegram digest failed: %s", exc)
        return False
