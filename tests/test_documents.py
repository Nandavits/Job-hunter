

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


def test_cv_photo_is_made_sharp_and_print_sized(tmp_path):
    from PIL import Image
    from jobhunter.documents import _sharp_photo, photo_too_small
    big = tmp_path / "big.jpg"
    Image.new("RGB", (1200, 1600), (120, 130, 140)).save(big)
    out = _sharp_photo(big)
    with Image.open(out) as im:
        assert im.size == (660, 850)
    small = tmp_path / "small.jpg"
    Image.new("RGB", (200, 260)).save(small)
    assert photo_too_small(small) and not photo_too_small(big)
