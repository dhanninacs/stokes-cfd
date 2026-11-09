"""`ID_RE` anchors at `\\Z`, so nothing follows the id (Opus review item 17).

**Why it matters**: an id becomes a PATH SEGMENT - `fields_path`, `_record_folder` and the upload
target all join one onto a directory - and `$` matches *before* a trailing newline. Measured:
`ID_RE.match("r_abc12345\\n")` was True, so `is_id` accepted it and the paths accepted it too.

It was a 404 in practice rather than a traversal, and that is exactly the kind of defect that is
worth closing anyway: "an id with a newline in it" is not an id, and the anchor is one place to say
so rather than a `fullmatch` at every call site.
"""

from __future__ import annotations

import pytest

from stokes.model.ids import ID_RE, is_id, new_id


def test_an_id_with_a_trailing_newline_is_not_an_id() -> None:
    """**The measurement the change is for.** `$` matches before the newline; `\\Z` does not."""
    assert ID_RE.match("r_abc12345\n") is None, "the pattern still anchors at `$`"
    assert ID_RE.fullmatch("r_abc12345\n") is None
    assert is_id("r_abc12345\n") is False


@pytest.mark.parametrize(
    "bad",
    [
        "r_abc12345\n",
        "r_abc12345 ",
        "r_abc12345 x",
        "../IMPORTANT",
        "/etc/passwd",
        "r_abc1234",  # seven hex digits
        "r_abc123456",  # nine
        "R_ABC12345",  # upper case
        "",
    ],
)
def test_a_string_that_is_not_an_id_is_refused(bad: str) -> None:
    assert is_id(bad) is False
    assert ID_RE.match(bad) is None


def test_a_real_id_is_still_an_id() -> None:
    """**The false-refusal half.** Every id this module makes must pass its own test."""
    for prefix in ("n", "r", "j", "e", "abcd"):
        made = new_id(prefix)
        assert is_id(made) is True, made
        assert ID_RE.fullmatch(made) is not None
