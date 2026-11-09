"""File names into node names: what a part is called (Claude's package-two review item 10).

**The rule this pins**: a generated file's content hash belongs to the FILE and not to the node. The
hash exists so a rebuilt sample is a different file only when its bytes differ, which fixed a real
defect - a fixed name rewrote the same file, a saved project's source looked modified and its tags
went stale. But the part inherited the file's name, so a user read `demo_pipe_e4977bbfe7f9` in the
tree, where a name belongs.

**The half that matters more is what is NOT stripped**: `flange_2` and `inlet_2024` are names a
person chose, and a rule that removed everything after the last underscore would rename them -
worse than the hash it removes.
"""

from __future__ import annotations

from pathlib import Path

from stokes.geometry.files import node_name_for, part_name_for


def test_a_content_hash_is_stripped_from_a_part_name() -> None:
    """A part is called `demo_pipe`, not `demo_pipe_e4977bbfe7f9`."""
    assert part_name_for(Path("demo_pipe_e4977bbfe7f9.step")) == "demo_pipe"
    # A dash as well as an underscore: both are how a tool joins a hash on.
    assert part_name_for(Path("duct-0123456789abcdef.step")) == "duct"


def test_a_real_name_that_ends_in_digits_is_kept() -> None:
    """**The half that matters more.**

    Eight or more hex characters is the shape a generated hash has and a name almost never
    does, so that is the rule rather than "looks like an id".
    """
    for name in ("flange_2", "inlet_2024", "bracket", "part_1a2b3c"):
        assert part_name_for(Path(f"{name}.step")) == name, name
    # **Seven characters is not a hash**, and a name that is ONLY a hash keeps it rather than
    # becoming
    # empty - which the model would refuse, turning a cosmetic rule into a failure.
    assert part_name_for(Path("abcdefg.step")) == "abcdefg"
    assert part_name_for(Path("deadbeefcafe.step")) == "deadbeefcafe"


def test_an_ordinary_file_name_still_becomes_a_node_name() -> None:
    """The existing behaviour, so the strip cannot have changed more than it should.

    A stem is cleaned into a valid node name by `node_name_for`, which is where spaces and
    punctuation are handled.
    """
    assert part_name_for(Path("my bracket.step")) == node_name_for("my bracket")
    assert part_name_for(Path("bracket.step")) == "bracket"
