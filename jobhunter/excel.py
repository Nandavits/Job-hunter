"""applications.xlsx: your application tracker in Excel, kept in sync with the job database.

- Every run (and every apply) rewrites the file from the database.
- Before that, statuses and notes you changed in Excel are read back into the database. So if you
  applied to a job outside Job Hunter (e.g. LinkedIn Easy Apply), set its Status to "applied" in
  Excel and save: reposts of that role will be skipped from then on.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .dedupe import APPLIED_STATUSES
from .storage import STATUSES, Store

log = logging.getLogger("jobhunter")

COLUMNS = [  # (header, width)
    ("ID", 11), ("Status", 12), ("Applied on", 12), ("Score", 7), ("Company", 28), ("Title", 46),
    ("Location", 22), ("Source", 12), ("Link", 14), ("Posted", 11), ("CV", 12), ("Notes", 40), ("Why skipped", 48),
]
SHEETS = {
    "Applications": lambda r: r["status"] in APPLIED_STATUSES,
    "Open jobs": lambda r: r["status"] in ("new", "prepared"),
    "All jobs": lambda r: True,
}
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
STATUS_FILLS = {"applied": "DDEBF7", "interview": "E2EFDA", "offer": "C6E0B4", "rejected": "F8CBAD",
                "prepared": "FFF2CC", "skipped": "EDEDED"}


def _row(r: dict) -> list:
    return [r["uid"][:8], r["status"], r.get("applied_at") or "", r.get("score") or 0, r.get("company") or "",
            r.get("title") or "", r.get("location") or "", r.get("source") or "",
            r.get("apply_url") or r.get("url") or "", r.get("posted_at") or "",
            "yes" if r.get("cv_path") else "", r.get("notes") or "", r.get("excluded_reason") or ""]


def export_excel(store: Store, path: Path) -> Path:
    rows = store.list(limit=100_000, include_skipped=True)
    rows.sort(key=lambda r: (r.get("applied_at") or "", r.get("collected_at") or ""), reverse=True)
    wb = Workbook()
    wb.remove(wb.active)
    for name, keep in SHEETS.items():
        ws = wb.create_sheet(name)
        ws.append([h for h, _ in COLUMNS])
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(vertical="center")
        for r in (r for r in rows if keep(r)):
            ws.append(_row(r))
            line = ws.max_row
            link = ws.cell(line, 9)
            if link.value:
                link.hyperlink, link.value, link.font = link.value, "open", Font(color="0563C1", underline="single")
            fill = STATUS_FILLS.get(r["status"])
            if fill:
                ws.cell(line, 2).fill = PatternFill("solid", fgColor=fill)
        for i, (_, width) in enumerate(COLUMNS, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(ws.max_row, 2)}"
        rule = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=False)
        rule.prompt, rule.promptTitle = "Change the status, save, and Job Hunter picks it up on its next run.", "Status"
        ws.add_data_validation(rule)
        rule.add(f"B2:B{max(ws.max_row, 2) + 500}")
    # Hidden copy of what was written, so the next sync only applies cells the person changed.
    snap = wb.create_sheet("_written")
    snap.sheet_state = "hidden"
    for r in rows:
        snap.append([r["uid"][:8], r["status"], r.get("notes") or ""])

    readme = wb.create_sheet("How to use")
    for line in [
        "How this file works",
        "",
        "Applications: jobs you applied to (applied / interview / offer / rejected).",
        "Open jobs: jobs found and not applied to yet. All jobs: everything, including skipped ones and why.",
        "",
        "Applied somewhere yourself (e.g. LinkedIn Easy Apply)? Set Status to 'applied' and save the file.",
        "On its next run Job Hunter records it, and reposts of that job are skipped automatically.",
        "You can also change statuses to interview / offer / rejected, and write Notes.",
        "Please don't edit the ID column. Close the file before Job Hunter runs, so it can update it.",
        f"Last updated: {date.today().isoformat()}",
    ]:
        readme.append([line])
    readme["A1"].font = Font(bold=True, size=13)
    readme.column_dimensions["A"].width = 110

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError:  # the file is open in Excel (Windows locks it)
        alt = path.with_name(path.stem + " (new).xlsx")
        wb.save(alt)
        log.warning("%s is open in Excel; wrote %s instead. Close Excel to update the main file.", path.name, alt.name)
        return alt
    return path


def sync_from_excel(store: Store, path: Path) -> int:
    """Read statuses / notes edited in Excel back into the database. Returns the number of changes."""
    if not path.exists():
        return 0
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # corrupt or not an Excel file
        log.warning("Could not read %s: %s", path.name, exc)
        return 0
    written: dict[str, tuple[str, str]] = {}
    if "_written" in wb.sheetnames:
        for row in wb["_written"].iter_rows(values_only=True):
            if row and row[0]:
                written[str(row[0])] = (str(row[1] or "").lower(), str(row[2] or "").strip())
    changes = 0
    seen: set[str] = set()
    for name in SHEETS:
        if name not in wb.sheetnames:
            continue
        for row in wb[name].iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue
            uid, status = str(row[0]).strip(), str(row[1] or "").strip().lower()
            notes = str(row[11] or "").strip() if len(row) > 11 else ""
            if uid in seen:
                continue
            seen.add(uid)
            old_status, old_notes = written.get(uid, ("", ""))
            job = store.get(uid)
            if not job:
                continue
            # Only what the person edited in Excel counts; unchanged cells never overwrite newer database values.
            if status in STATUSES and status != old_status and status != job["status"]:
                if status == "applied":
                    store.mark_applied(job["uid"])
                else:
                    store.update(job["uid"], status=status)
                changes += 1
            if notes and notes != old_notes and notes != (job.get("notes") or "").strip():
                store.update(job["uid"], notes=notes)
                changes += 1
    wb.close()
    if changes:
        log.info("Read %d change(s) from %s", changes, path.name)
    return changes
