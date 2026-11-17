from openpyxl import load_workbook

from jobhunter.excel import export_excel, sync_from_excel
from jobhunter.models import Job
from jobhunter.storage import Store


def _store(settings):
    store = Store(settings.database_url)
    store.save([
        Job(source="greenhouse", external_id="1", company="Acme GmbH", title="Data Engineer (m/w/d)", url="https://a/1"),
        Job(source="email", external_id="linkedin:2", company="Beta", title="Data Analyst", url="https://b/2"),
    ])
    return store


def _edit(path, sheet, uid, status=None, notes=None):
    wb = load_workbook(path)
    ws = wb[sheet]
    for row in ws.iter_rows(min_row=2):
        if row[0].value == uid[:8]:
            if status:
                row[1].value = status
            if notes:
                row[11].value = notes
    wb.save(path)


def test_export_has_sheets_links_and_status_dropdown(settings, tmp_path):
    store = _store(settings)
    a = store.list()[0]
    store.mark_applied(a["uid"])
    path = export_excel(store, tmp_path / "applications.xlsx")
    wb = load_workbook(path)
    assert wb.sheetnames[:3] == ["Applications", "Open jobs", "All jobs"]
    assert wb["_written"].sheet_state == "hidden"
    apps = list(wb["Applications"].iter_rows(min_row=2, values_only=True))
    assert len(apps) == 1 and apps[0][1] == "applied" and apps[0][2]  # applied date filled
    assert wb["Applications"]["I2"].hyperlink.target.startswith("https://")
    assert wb["Applications"].data_validations.dataValidation


def test_applied_on_linkedin_marked_in_excel_then_repost_skipped(settings, tmp_path):
    from jobhunter.pipeline import skip_known_jobs
    store = _store(settings)
    beta = next(j for j in store.list() if j["company"] == "Beta")
    path = export_excel(store, tmp_path / "applications.xlsx")
    _edit(path, "Open jobs", beta["uid"], status="applied", notes="Easy Apply on LinkedIn")
    assert sync_from_excel(store, path) == 2
    assert store.get(beta["uid"])["status"] == "applied"
    assert store.get(beta["uid"])["notes"] == "Easy Apply on LinkedIn"

    repost = Job(source="email", external_id="linkedin:99", company="Beta GmbH", title="Data Analyst (m/w/d)", url="x")
    store.save([repost])
    fresh, reposts, _ = skip_known_jobs(store, [store.get(repost.uid)])
    assert not fresh and len(reposts) == 1


def test_old_excel_never_undoes_newer_changes(settings, tmp_path):
    store = _store(settings)
    acme = next(j for j in store.list() if j["company"] == "Acme GmbH")
    path = export_excel(store, tmp_path / "applications.xlsx")   # Excel says "new"
    store.mark_applied(acme["uid"])                                # then applied via apply.bat
    assert sync_from_excel(store, path) == 0                       # untouched Excel row changes nothing
    assert store.get(acme["uid"])["status"] == "applied"


def test_status_change_to_interview(settings, tmp_path):
    store = _store(settings)
    acme = next(j for j in store.list() if j["company"] == "Acme GmbH")
    store.mark_applied(acme["uid"])
    path = export_excel(store, tmp_path / "applications.xlsx")
    _edit(path, "Applications", acme["uid"], status="interview")
    sync_from_excel(store, path)
    assert store.get(acme["uid"])["status"] == "interview"


def test_file_open_in_excel_writes_a_copy(settings, tmp_path, monkeypatch):
    from openpyxl.workbook import workbook
    store = _store(settings)
    target = tmp_path / "applications.xlsx"
    real_save = workbook.Workbook.save

    def save(self, filename):
        if str(filename) == str(target):
            raise PermissionError("locked by Excel")
        return real_save(self, filename)
    monkeypatch.setattr(workbook.Workbook, "save", save)
    written = export_excel(store, target)
    assert written.name == "applications (new).xlsx" and written.exists()


def test_run_writes_excel(settings, session):
    from jobhunter.pipeline import run
    result = run(settings, send_digest=False, session=session)
    wb = load_workbook(result["excel"])
    assert len(list(wb["Open jobs"].iter_rows(min_row=2))) == result["new"]
