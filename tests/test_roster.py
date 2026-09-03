from pathlib import Path

from app.roster import correct_employee_name, load_roster

ROSTER = ("Lorence Berganting", "Alfonso Luis Alcoy", "John Renzo Dacer", "Jan Aubrey Azures")


def test_corrects_misspelled_name_with_honorific():
    assert correct_employee_name("Sir Jan Audrey", ROSTER) == "Jan Aubrey Azures"


def test_corrects_minor_typo():
    assert correct_employee_name("Alfonso Luiz Alcoy", ROSTER) == "Alfonso Luis Alcoy"


def test_leaves_unmatched_name_unchanged():
    assert correct_employee_name("Random New Guy", ROSTER) == "Random New Guy"


def test_leaves_name_unchanged_when_roster_empty():
    assert correct_employee_name("Jan Aubrey Azures", ()) == "Jan Aubrey Azures"


def test_abbreviated_first_name_is_kept_as_sent():
    # "A. Azures" would otherwise fuzzy-match "Jan Aubrey Azures" -- keep as sent.
    assert correct_employee_name("A. Azures", ROSTER) == "A. Azures"
    assert correct_employee_name("J Dacer", ROSTER) == "J Dacer"


def test_load_roster_skips_blank_lines_comments_and_numbering(tmp_path: Path):
    roster_file = tmp_path / "employees.txt"
    roster_file.write_text(
        "# Canonical roster\n\n1. Lorence Berganting\n2) Alfonso Luis Alcoy\n\n# trailing comment\n",
        encoding="utf-8",
    )
    assert load_roster(roster_file) == ("Lorence Berganting", "Alfonso Luis Alcoy")


def test_load_roster_returns_empty_tuple_when_file_missing(tmp_path: Path):
    assert load_roster(tmp_path / "missing.txt") == ()
