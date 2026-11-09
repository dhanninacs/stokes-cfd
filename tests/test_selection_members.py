"""Named-tag members (ADR-0020 slice 1): the stored JSON, parsing command input, range and
member arithmetic, invert. No meshes, no OCP."""

from __future__ import annotations

import json

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from stokes.geometry.errors import SelectionError
from stokes.geometry.selection import (
    Member,
    Target,
    complement,
    components,
    difference,
    dump_members,
    invert,
    load_members,
    merge,
    parse_members,
    ranges_by_part,
    ranges_from_ids,
    ranges_from_mask,
    subtract,
    union,
)

A, B, C = "n_0000000a", "n_0000000b", "n_0000000c"
COUNTS = {A: 10, B: 6, C: None}


def count(part: str) -> int | None:
    return COUNTS.get(part)


def resolve(ref: str) -> list[Target]:
    groups = {"/geometry/parts": [A, B], "/geometry/parts/a": [A]}
    ids = groups.get(ref, [ref] if ref in COUNTS else None)
    if ids is None:
        raise KeyError(ref)
    return [Target(i, COUNTS[i]) for i in ids]


def test_the_stored_text_is_canonical_and_round_trips() -> None:
    # `{"part": B}` alone is now REFUSED as command input (the owner's answer 4, via Claude): a
    # member naming no faces, edges or vertices is malformed, not "every face". "Every face" has its
    # own spelling, `faces: "all"`, which is what the stored text writes too. The test
    # `test_a_member_that_names_nothing_is_refused` already asserted this for stored text; the two
    # paths now agree instead of the input path accepting what the loader refused.
    members = parse_members(
        [
            {"part": A, "faces": [5, [0, 2], 3, [7, 9], 8]},
            {"part": B, "faces": "all"},
            {"part": A, "faces": [4]},
        ],
        resolve,
    )
    assert members == (Member(A, ((0, 5), (7, 9))), Member(B, None))
    text = dump_members((Member(A, ((0, 5), (7, 9)), "abc123"), Member(B, None, "ignored")))
    assert text == '[{"part":"n_0000000a","faces":[[0,5],[7,9]],"basis":"abc123"},' + (
        '{"part":"n_0000000b","faces":"all"}]'
    )
    assert load_members(text) == (Member(A, ((0, 5), (7, 9)), "abc123"), Member(B, None))
    assert load_members("[]") == ()


def test_a_bare_member_is_refused_as_command_input() -> None:
    """The owner's answer 4: `{"part": x, "faces": "all"}` is malformed, not "no members"."""
    with pytest.raises(SelectionError, match="names nothing"):
        parse_members([{"part": A}], resolve)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        '[{"part": "n_0000000a"}]',  # faces missing: stored text is always explicit
        '[{"part": "nope", "faces": "all"}]',
        '[{"part": "n_0000000a", "faces": "all"}, {"part": "n_0000000a", "faces": "all"}]',
        '[{"part": "n_0000000a", "faces": [[3, 1]]}]',
        '[{"part": "n_0000000a", "faces": [[0, 1]], "basis": "XYZ"}]',
        '[{"part": "n_0000000a", "faces": "all", "colour": "red"}]',
    ],
)
def test_malformed_stored_members_are_refused(text: str) -> None:
    with pytest.raises(SelectionError, match="malformed"):
        load_members(text, where="/geometry/tags/s")


def test_parse_resolves_groups_and_checks_ids() -> None:
    assert parse_members([{"part": "/geometry/parts", "faces": "all"}], resolve) == (
        Member(A, None),
        Member(B, None),
    )
    with pytest.raises(SelectionError, match="face ids need one part"):
        parse_members([{"part": "/geometry/parts", "faces": [0]}], resolve)
    with pytest.raises(SelectionError, match="has 6 faces"):
        parse_members([{"part": B, "faces": [6]}], resolve)
    # A part whose face count is unknown (not read yet) takes any non-negative id.
    assert parse_members([{"part": C, "faces": [[40, 41]]}], resolve) == (Member(C, ((40, 41),)),)
    for bad in ([{"part": A, "faces": [-1]}], [{"part": A, "faces": [True]}], [{"faces": "all"}]):
        with pytest.raises(SelectionError):
            parse_members(bad, resolve)
    with pytest.raises(SelectionError, match="must be a list"):
        parse_members({"part": A}, resolve)
    # A basis given by a client is ignored: the commands stamp their own.
    assert parse_members([{"part": A, "faces": [1], "basis": "ff"}], resolve) == (
        Member(A, ((1, 1),)),
    )


def test_union_difference_and_whole_parts() -> None:
    have = (Member(A, ((0, 2),), "b1"), Member(B, None))
    more = union(have, (Member(A, ((3, 4), (8, 8))), Member(C, ((1, 1),))))
    assert more == (Member(A, ((0, 4), (8, 8)), "b1"), Member(B, None), Member(C, ((1, 1),)))
    assert union(have, (Member(A, None),))[0] == Member(A, None)
    less = difference(more, (Member(A, ((1, 3),)), Member(B, ((0, 0), (5, 5)))), count)
    assert less == (Member(A, ((0, 0), (4, 4), (8, 8)), "b1"), Member(B, ((1, 4),)), more[2])
    assert difference(more, (Member(C, None),), count) == more[:2]
    with pytest.raises(SelectionError, match="not known yet"):
        difference((Member(C, None),), (Member(C, ((0, 0),)),), count)


def test_invert_within_parts() -> None:
    have = (Member(A, ((0, 4),)), Member(B, None), Member(C, ((0, 0),)))
    flipped = invert(have, [A, B], count)
    assert flipped == (Member(A, ((5, 9),)), Member(C, ((0, 0),)))
    assert invert(flipped, [A, B], count) == (
        Member(A, ((0, 4),)),
        Member(C, ((0, 0),)),
        Member(B, None),
    )
    assert invert((), [B], count) == (Member(B, None),)
    with pytest.raises(SelectionError):
        invert(have, [C], count)


def test_member_expansion() -> None:
    member = Member(A, ((1, 3), (7, 7)))
    assert member.ids(None).tolist() == [1, 2, 3, 7]
    assert member.size(None) == 4 and member.beyond(5) == [7]
    assert Member(B, None).ids(3).tolist() == [0, 1, 2]
    assert Member(B, None).size(None) is None
    assert json.loads(dump_members([member]))[0]["faces"] == [[1, 3], [7, 7]]


def test_ranges_from_masks_and_ids() -> None:
    assert ranges_from_mask(np.array([1, 1, 0, 1, 0, 0, 1], dtype=bool)) == ((0, 1), (3, 3), (6, 6))
    assert ranges_from_mask(np.zeros(4, dtype=bool)) == ()
    assert ranges_from_ids([9, 3, 4, 5, 3]) == ((3, 5), (9, 9))
    assert ranges_from_ids([]) == ()


ids = st.sets(st.integers(min_value=0, max_value=59), max_size=40)


@settings(deadline=None)  # timing is not under test; a busy machine trips the default
@given(ids, ids)
def test_range_arithmetic_matches_sets(left: set[int], right: set[int]) -> None:
    a, b = ranges_from_ids(sorted(left)), ranges_from_ids(sorted(right))

    def members(ranges: tuple[tuple[int, int], ...]) -> set[int]:
        return {i for first, last in ranges for i in range(first, last + 1)}

    assert members(merge(a + b)) == left | right
    assert members(subtract(a, b)) == left - right
    assert members(complement(a, 60)) == set(range(60)) - left
    assert subtract(a, b) == ranges_from_ids(sorted(left - right))


@settings(deadline=None)
@given(
    st.integers(min_value=1, max_value=40),
    st.lists(st.tuples(st.integers(0, 39), st.integers(0, 39)), max_size=60),
)
def test_components_match_scipy(count: int, pairs: list[tuple[int, int]]) -> None:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    links = np.array([(a, b) for a, b in pairs if a < count and b < count], dtype=np.int64)
    links = links.reshape(-1, 2)
    labels = components(links, count)
    graph = coo_matrix((np.ones(len(links)), (links[:, 0], links[:, 1])), shape=(count, count))
    _, expected = connected_components(graph, directed=False)
    for node in range(count):  # same partition, and each label is its component's least node
        same = expected == expected[node]
        assert (labels[same] == labels[node]).all() and (labels[~same] != labels[node]).all()
        assert labels[node] == np.flatnonzero(same).min()


@settings(deadline=None)
@given(st.lists(st.lists(st.booleans(), max_size=12), max_size=8))
def test_ranges_by_part_match_each_part_alone(masks: list[list[bool]]) -> None:
    sizes = [len(m) for m in masks]
    offsets = np.cumsum([0, *sizes]).astype(np.int64)
    joined = np.array([b for m in masks for b in m], dtype=bool)
    assert ranges_by_part(joined, offsets) == [ranges_from_mask(np.array(m, bool)) for m in masks]


# ------------------------------------------- edge and vertex members (ADR-0022 §15.1, T-056)


def test_an_old_member_parses_to_exactly_what_it_did_before() -> None:
    """Backward compatibility, stated as a test: pre-existing text gains no edge members."""
    before = (Member(A, ((0, 2),), "abc123"), Member(B, None))
    text = '[{"part":"n_0000000a","faces":[[0,2]],"basis":"abc123"},' + (
        '{"part":"n_0000000b","faces":"all"}]'
    )
    assert load_members(text) == before
    for member in before:
        assert member.edges == () and member.vertices == ()


def test_a_face_only_member_is_written_byte_for_byte_as_before() -> None:
    """The new keys are additive: a face-only tag's stored text does not change."""
    text = dump_members((Member(A, ((0, 5), (7, 9)), "abc123"), Member(B, None)))
    assert text == (
        '[{"part":"n_0000000a","faces":[[0,5],[7,9]],"basis":"abc123"},'
        '{"part":"n_0000000b","faces":"all"}]'
    )


def test_an_edge_member_round_trips() -> None:
    member = Member(A, (), "abc123", ((3, 4), (9, 9)))
    text = dump_members((member,))
    assert load_members(text) == (member,)
    assert json.loads(text)[0]["edges"] == [[3, 4], [9, 9]]


def test_a_vertex_member_round_trips() -> None:
    member = Member(A, (), "abc123", (), ((0, 0), (7, 8)))
    assert load_members(dump_members((member,))) == (member,)


def test_a_member_may_hold_faces_edges_and_vertices_together() -> None:
    """§15.4's mode conversion produces these, so one member must be able to say all three."""
    member = Member(A, ((1, 2),), "abc123", ((0, 3),), ((5, 5),))
    assert load_members(dump_members((member,))) == (member,)


def test_a_whole_face_set_with_explicit_edges_keeps_both_meanings() -> None:
    """ "all faces plus these edges" is expressible and must survive the round trip."""
    member = Member(A, None, "abc123", ((2, 2),))
    text = dump_members((member,))
    stored = json.loads(text)[0]
    assert stored["faces"] == "all" and stored["edges"] == [[2, 2]]
    assert load_members(text) == (member,)


def test_a_member_with_no_explicit_ids_has_no_basis() -> None:
    """A basis names the version explicit ids refer to, so a wholly whole member drops it."""
    assert "basis" not in dump_members((Member(A, None, "abc123"),))
    assert Member(A, None).explicit is False


def test_a_basis_is_refused_when_nothing_is_explicit() -> None:
    bad = '[{"part":"n_0000000a","faces":"all","basis":"abc123"}]'
    with pytest.raises(SelectionError, match="basis"):
        load_members(bad, where="/geometry/tags/s")


def test_a_basis_is_allowed_with_only_edges() -> None:
    """All three id sets share the member's basis; an edge-only member still names one."""
    text = '[{"part":"n_0000000a","faces":[],"edges":[[0,0]],"basis":"abc123"}]'
    assert load_members(text) == (Member(A, (), "abc123", ((0, 0),)),)


def test_an_edge_only_member_omits_faces() -> None:
    """ADR-0022 §15.4: "new edge/point-only members omit `faces`"."""
    member = Member(A, (), "abc123", ((0, 1),))
    text = dump_members((member,))
    assert "faces" not in json.loads(text)[0]
    assert load_members(text) == (member,)


def test_a_member_that_names_nothing_is_refused() -> None:
    """The historical rule stands: stored text is always explicit about something."""
    with pytest.raises(SelectionError, match="names nothing"):
        load_members('[{"part":"n_0000000a"}]', where="/geometry/tags/s")


def test_malformed_edge_and_vertex_sets_are_refused() -> None:
    for bad in (
        '[{"part":"n_0000000a","faces":[],"edges":"some"}]',
        '[{"part":"n_0000000a","faces":[],"edges":[[3,1]]}]',
        '[{"part":"n_0000000a","faces":[],"edges":[-1]}]',
        '[{"part":"n_0000000a","faces":[],"vertices":[[1,2,3]]}]',
        '[{"part":"n_0000000a","faces":[],"edges":[[0,0]],"who":1}]',
    ):
        with pytest.raises(SelectionError, match="malformed"):
            load_members(bad, where="/geometry/tags/s")


def test_parse_takes_edge_and_vertex_ids_from_a_client() -> None:
    members = parse_members(
        [{"part": A, "faces": [], "edges": [1, [3, 5]], "vertices": [7]}], resolve
    )
    assert members == (Member(A, (), None, ((1, 1), (3, 5)), ((7, 7),)),)


def test_parse_refuses_edge_ids_for_a_group_of_parts() -> None:
    with pytest.raises(SelectionError, match="one part"):
        parse_members([{"part": "/geometry/parts", "faces": "all", "edges": [0]}], resolve)


def test_union_merges_edges_and_vertices_independently() -> None:
    have = (Member(A, ((0, 1),), "b1", ((0, 0),)),)
    more = union(have, (Member(A, ((2, 3),), None, ((1, 2),), ((9, 9),)),))
    assert more == (Member(A, ((0, 3),), "b1", ((0, 2),), ((9, 9),)),)


def test_a_whole_face_union_keeps_the_edge_sets() -> None:
    """A whole part absorbs the faces, but explicit edges on either side are still the tag's."""
    merged = union(
        (Member(A, ((0, 1),), "b1", ((5, 5),)),), (Member(A, None, None, (), ((7, 7),)),)
    )
    assert len(merged) == 1
    assert merged[0].faces is None
    assert merged[0].edges == ((5, 5),)
    assert merged[0].vertices == ((7, 7),)


def test_difference_removes_edges_and_vertices_too() -> None:
    have = (Member(A, ((0, 5),), "b1", ((0, 3),), ((1, 2),)),)
    drop = (Member(A, (), None, ((1, 1),), ()),)
    out = difference(have, drop, count)
    # The surviving ids still refer to `have`'s basis, so it is kept.
    assert out == (Member(A, ((0, 5),), "b1", ((0, 0), (2, 3)), ((1, 2),)),)


def test_dropping_every_edge_leaves_nothing() -> None:
    """A whole edge set minus a whole edge set is empty, not "all edges" (a real bug once)."""
    have = (Member(A, (), "b1", None),)
    drop = (Member(A, (), None, None),)
    assert difference(have, drop, count) == ()


def test_difference_refuses_to_complement_edges_it_cannot_count() -> None:
    """All edges minus named edges needs the edge total; inventing one would corrupt the tag."""
    have = (Member(A, (), "b1", None),)
    drop = (Member(A, (), None, ((1, 1),)),)
    with pytest.raises(SelectionError, match="all of them"):
        difference(have, drop, count)


def test_an_all_empty_result_is_dropped_from_the_member_list() -> None:
    """As faces always did: a member that names nothing is not stored."""
    have = (Member(A, (), "b1", ((0, 0),)),)
    drop = (Member(A, (), None, ((0, 0),)),)
    assert difference(have, drop, count) == ()


def test_invert_carries_edge_members_through_untouched() -> None:
    have = (Member(A, ((0, 2),), "b1", ((4, 4),)),)
    out = invert(have, [A], count)
    assert out == (Member(A, ((3, 9),), "b1", ((4, 4),)),)


def test_a_member_is_empty_only_when_it_names_nothing() -> None:
    assert Member(A, (), None, (), ()).empty is True
    assert Member(A, (), None, ((0, 0),), ()).empty is False
    assert Member(A, None).empty is False
