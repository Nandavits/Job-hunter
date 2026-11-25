

def test_cv_photo_top_right_when_profile_names_one(tmp_path):
    import yaml
    from docx import Document
    from PIL import Image
    from jobhunter.config import ROOT
    from jobhunter.documents import build_cv, photo_path
    from jobhunter.tailor import Tailoring
    Image.new("RGB", (350, 450), (200, 200, 200)).save(tmp_path / "me.jpg")
    prof = yaml.safe_load((ROOT / "config" / "profile.example.yaml").read_text())
    t = Tailoring(title="Data Engineer", summary="s", bullet_order={}, skills_order=list(prof["skills"]),
                  letter_paragraphs=["a"])
    assert photo_path(prof) is None
    build_cv(prof, t, tmp_path / "plain.docx")
    assert not Document(tmp_path / "plain.docx").inline_shapes
    prof["personal"]["photo"] = str(tmp_path / "me.jpg")
    build_cv(prof, t, tmp_path / "photo.docx")
    doc = Document(tmp_path / "photo.docx")
    assert len(doc.inline_shapes) == 1
    assert doc.tables[0].rows[0].cells[0].paragraphs[0].text.startswith(prof["personal"]["first_name"])
    prof["personal"]["photo"] = str(tmp_path / "missing.jpg")
    assert photo_path(prof) is None
