from pathlib import Path

from jobhunter.checksettings import check_text

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = (ROOT / "config" / "profile.example.yaml").read_text(encoding="utf-8")


def _report(text, name="profile.yaml"):
    ok, lines = check_text(text, name)
    return ok, "\n".join(lines)


def test_github_line_without_label_points_at_that_line():
    broken = EXAMPLE.replace("  github: https://github.com/janedoe", "  https://github.com/janedoe")
    ok, report = _report(broken)
    assert not ok and "Look at line 12" in report and "no label and colon" in report


def test_github_left_with_a_space_instead_of_colon():
    broken = EXAMPLE.replace("  github: https://github.com/janedoe", "  github ")
    ok, report = _report(broken)
    assert not ok and "line 12" in report


def test_colon_inside_a_sentence_is_explained():
    broken = EXAMPLE.replace("- text: Own the analytics layer", "- text: Tools: Own the analytics layer", 1)
    ok, report = _report(broken)
    assert not ok and "double quotes" in report


def test_tab_is_found():
    broken = EXAMPLE.replace("  first_name: Jane", "\tfirst_name: Jane")
    ok, report = _report(broken)
    assert not ok and "Tab" in report


def test_example_reads_but_still_has_the_example_name():
    ok, report = _report(EXAMPLE)
    assert not ok and "example name" in report


def test_a_filled_in_profile_is_ok():
    mine = EXAMPLE.replace("first_name: Jane", "first_name: Asha").replace("last_name: Doe", "last_name: Rao")
    ok, report = _report(mine)
    assert ok, report


def test_config_example_is_ok():
    ok, report = _report((ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"), "config.yaml")
    assert ok, report
