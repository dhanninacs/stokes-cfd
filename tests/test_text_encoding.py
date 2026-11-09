"""The encoding gate, so a wrong-encoding rewrite fails a test rather than a picture review.

**Why this exists.** A picture of the Properties panel showed ``Source  â€"`` and ``0.5 Ã— 0.02``:
the signature of UTF-8 read as Windows-1252 and written back. It shipped because nothing in the
suite
looked at the BYTES of the text files: every test asserted behaviour, and the behaviour was fine.

`tools/check_encoding.py` holds the rule; this calls it, so the gate can be run by hand and by CI
without the two disagreeing about what "damaged" means.
"""

from __future__ import annotations

from pathlib import Path

from tools.check_encoding import check, damage_in


def test_no_tracked_text_file_holds_wrongly_decoded_text() -> None:
    """The tree, as a whole."""
    findings = check(Path(__file__).resolve().parent.parent)
    assert findings == [], "\n".join(findings)


def test_the_check_NOTICES_the_damage_it_exists_for() -> None:
    """**The gate proves it can fail.** A rule that cannot fail reads as coverage while covering
    nothing, and this one's whole subject is an invisible failure - the characters look like text.

    The two strings from the picture are an em dash and a multiplication sign, each decoded as
    cp1252 and re-encoded.

    **The damaged text is BUILT here, not written into this file.** My first version held the
    mojibake as a literal, and the gate then refused its own test - correctly, because a repository
    holding damaged bytes is the thing being prevented, and a fixture is still bytes in a tracked
    file. Building it from the clean characters keeps this file clean while testing exactly the same
    times = (
        f'const size = [w, h].join(" {clean_times.encode("utf-8").decode("cp1252")} ");'
    )
    what `tools/check_encoding.py` would have a reader do.
    """
    clean_dash = "—"
    clean_times = "×"  # noqa: RUF001 - the character under test, not a typo for x
    em_dash = f'return value > 0 ? value : "{clean_dash.encode("utf-8").decode("cp1252")}";'
    times = f'const size = [w, h].join(" {clean_times.encode("utf-8").decode("cp1252")} ");'
    assert damage_in(em_dash), "an em dash that was double-encoded is not noticed"
    assert damage_in(times), "a multiplication sign that was double-encoded is not noticed"
    # And the repaired forms are clean, so the rule is not simply refusing any non-ASCII text.
    assert damage_in(f'const dash = "{clean_dash}";') == []
    assert damage_in(f'const size = [w, h].join(" {clean_times} ");') == []
    assert damage_in("x = 1  # a plain comment") == []


def test_a_legitimate_accent_is_not_refused() -> None:
    """A file may hold real non-ASCII text. **The rule is the round-trip, not the characters.**

    `Müller` is valid UTF-8 that does not reverse through cp1252 into something else, so it is left
    alone - which is the difference between this gate and a grep for a list of suspicious sequences.
    """
    assert damage_in('author = "Müller"') == []
    assert damage_in('const unit = "µm";') == []
