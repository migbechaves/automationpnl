import difflib
import re
from functools import lru_cache
from pathlib import Path

# Titles/honorifics that sometimes get typed alongside a name in a caption but
# never appear in the roster itself -- stripped before matching so they don't
# drag the similarity score down.
_HONORIFICS = {"sir", "maam", "ma'am", "mr", "mrs", "ms", "miss"}

# "F. Dela Cruz" / "F Cruz" -- an initial standing in for the first name. Too
# little to fuzzy-match against a full-name roster (the match just picks a wrong
# full name), so these are recorded exactly as sent.
_ABBREVIATED_FIRST_NAME = re.compile(r"^[A-Za-z](\.\s*|\s+)\S")


def _strip_honorifics(name: str) -> str:
    tokens = [token for token in re.split(r"\s+", name.strip()) if token]
    cleaned = [token for token in tokens if token.strip(".").lower() not in _HONORIFICS]
    return " ".join(cleaned) if cleaned else name.strip()


@lru_cache(maxsize=8)
def load_roster(path: Path) -> tuple[str, ...]:
    """Load the canonical employee roster from a plain text file, one name per line.

    Blank lines and lines starting with "#" are ignored; a leading "1. "/"1) "
    numbering prefix is stripped so a numbered list (as in the README) also
    works. Cached per path for the life of the process -- restart the bot to
    pick up edits to the roster file.
    """
    if not path.exists():
        return ()
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if line:
            names.append(line)
    return tuple(names)


def correct_employee_name(name: str, roster: tuple[str, ...], cutoff: float = 0.6) -> str:
    """Return the closest canonical roster spelling for `name`, or `name` unchanged
    if nothing in the roster is a close enough match (e.g. a new employee who
    isn't in the roster yet -- left as typed rather than mismatched).

    Names given with an abbreviated first name ("F. Dela Cruz") are also left as
    sent -- an initial is too little to fuzzy-match a full-name roster safely.
    """
    if not name or not roster:
        return name
    candidate = _strip_honorifics(name)
    if not candidate:
        return name
    if _ABBREVIATED_FIRST_NAME.match(candidate):
        return name
    lowered_roster = [entry.lower() for entry in roster]
    matches = difflib.get_close_matches(candidate.lower(), lowered_roster, n=1, cutoff=cutoff)
    if not matches:
        return name
    for entry in roster:
        if entry.lower() == matches[0]:
            return entry
    return name
