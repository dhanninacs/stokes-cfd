"""Property tests for the dictionary reader (ADR-0029, T-303).

Generated well-formed dictionaries, with random whitespace and comments between their tokens,
must round-trip byte for byte and parse back to the abstract tree they were generated from.
Arbitrary bytes must either parse (and then round-trip) or raise a `StokesError`, never anything
else. All input is synthetic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from stokes.compile.foam_read import (
    MAX_DEPTH,
    BlockNode,
    DictNode,
    DimsNode,
    DirectiveNode,
    Document,
    EntryNode,
    Kind,
    ListNode,
    Node,
    Token,
    parse,
    serialise,
)
from stokes.errors import StokesError

# ---------------------------------------------------------------- the abstract tree


@dataclass(frozen=True)
class Leaf:
    kind: str
    text: str


@dataclass(frozen=True)
class Group:
    kind: str  # "list", "dims", "dict"
    items: tuple[Abstract, ...]


@dataclass(frozen=True)
class Entry:
    key: Leaf
    values: tuple[Abstract, ...]


@dataclass(frozen=True)
class Block:
    key: Leaf
    body: tuple[Abstract, ...]


@dataclass(frozen=True)
class Directive:
    name: str
    argument: Abstract | None


type Abstract = Leaf | Group | Entry | Block | Directive

# ---------------------------------------------------------------- strategies

_words = st.from_regex(r"[A-Za-z_][A-Za-z0-9_.:<>,]{0,8}", fullmatch=True) | st.sampled_from(
    ["div(phi,U)", "laplacian((a*b),c)", "a/b", "uniform", "Gauss"]
)
_numbers = st.integers(-(10**9), 10**9).map(str) | st.floats(
    allow_nan=False, allow_infinity=False
).map(repr)
_strings = st.text(
    st.characters(min_codepoint=0x20, max_codepoint=0x7E, exclude_characters='"\\'), max_size=12
).map(lambda s: f'"{s}"')
_variables = _words.map(lambda w: f"${w}")

_word_leaf = _words.map(lambda t: Leaf("word", t))
_leaf = st.one_of(
    _word_leaf,
    _numbers.map(lambda t: Leaf("number", t)),
    _strings.map(lambda t: Leaf("string", t)),
    _variables.map(lambda t: Leaf("variable", t)),
)
_dims = st.lists(st.integers(-3, 3).map(str), min_size=7, max_size=7).map(
    lambda xs: Group("dims", tuple(Leaf("number", x) for x in xs))
)
_key = st.one_of(_word_leaf, _strings.map(lambda t: Leaf("string", t)))


def _body(values: st.SearchStrategy[Abstract]) -> st.SearchStrategy[tuple[Abstract, ...]]:
    # A first value that is a dictionary would make the entry a block, so it is not generated.
    first_not_dict = st.lists(values, min_size=1, max_size=4).filter(
        lambda vs: not (isinstance(vs[0], Group) and vs[0].kind == "dict")
    )
    entry = st.builds(Entry, _key, first_not_dict.map(tuple)) | st.builds(
        Entry, _variables.map(lambda t: Leaf("variable", t)), st.just(())
    )
    include = _strings.map(lambda t: Directive("include", Leaf("string", t)))
    return st.lists(entry | include, max_size=4).map(tuple)


def _extend(values: st.SearchStrategy[Abstract]) -> st.SearchStrategy[Abstract]:
    return st.one_of(
        st.lists(values, max_size=4).map(lambda xs: Group("list", tuple(xs))),
        _body(values).map(lambda b: Group("dict", b)),
    )


_values: st.SearchStrategy[Abstract] = st.recursive(_leaf | _dims, _extend, max_leaves=12)
_items = st.lists(
    st.one_of(
        _body(_values).flatmap(lambda b: _key.map(lambda k: Block(k, b))),
        _body(_values).map(lambda b: b[0] if b else Directive("include", Leaf("string", '"x"'))),
    ),
    max_size=5,
)

_GAPS = [" ", "\n", "\t", "  ", "\r\n", " /* note */ ", " // note\n", "\n\n    "]

# ---------------------------------------------------------------- render and abstract


def _render(item: Abstract, gap: Callable[[], str]) -> str:
    if isinstance(item, Leaf):
        return item.text
    if isinstance(item, Directive):
        assert item.argument is not None
        return f"#{item.name}{gap()}{_render(item.argument, gap)}"
    if isinstance(item, Entry):
        parts = [_render(item.key, gap), *(_render(v, gap) for v in item.values)]
        return gap().join(parts) + gap() + ";"
    if isinstance(item, Block):
        return _render(item.key, gap) + gap() + _render(Group("dict", item.body), gap)
    opener, closer = {"list": ("(", ")"), "dims": ("[", "]"), "dict": ("{", "}")}[item.kind]
    inner = [_render(i, gap) for i in item.items]
    return opener + gap() + gap().join(inner) + gap() + closer


def _abstract(node: Node) -> Abstract:
    if isinstance(node, Token):
        return Leaf(node.kind.value, node.text)
    if isinstance(node, EntryNode):
        key = _abstract(node.key)
        assert isinstance(key, Leaf)
        return Entry(key, tuple(_abstract(v) for v in node.values))
    if isinstance(node, BlockNode):
        key = _abstract(node.key)
        assert isinstance(key, Leaf)
        return Block(key, tuple(_abstract(i) for i in node.body.items))
    if isinstance(node, DirectiveNode):
        arg = node.argument
        return Directive(node.name, None if arg is None else _abstract(arg))
    kinds = {ListNode: "list", DimsNode: "dims", DictNode: "dict", Document: "doc"}
    return Group(kinds[type(node)], tuple(_abstract(i) for i in node.items))


@settings(max_examples=300, deadline=None)
@given(items=_items, data=st.data())
def test_generated_dictionaries_round_trip(items: list[Abstract], data: st.DataObject) -> None:
    def gap() -> str:
        return data.draw(st.sampled_from(_GAPS))

    text = gap() + gap().join(_render(i, gap) for i in items) + "\n"
    source = text.encode("ascii")
    doc = parse(source)
    assert serialise(doc) == source
    assert _abstract(doc) == Group("doc", tuple(items))
    assert parse(serialise(doc)) == doc


@settings(max_examples=500, deadline=None)
@given(st.binary(max_size=400))
def test_random_bytes_raise_only_stokes_errors(data: bytes) -> None:
    try:
        doc = parse(data)
    except StokesError:
        return
    assert serialise(doc) == data


_ALPHABET = [*list("abc019.-+e $#/*\"'\\{}()[];\n\t"), "#{", "#}", "#include ", "#includeFunc "]


@settings(max_examples=500, deadline=None)
@given(st.lists(st.sampled_from(_ALPHABET), max_size=60).map("".join))
def test_dictionary_shaped_noise_raises_only_stokes_errors(text: str) -> None:
    source = text.encode("ascii")
    try:
        doc = parse(source)
    except StokesError as err:
        assert err.code.startswith("foam.read")
        return
    assert serialise(doc) == source
    assert all(isinstance(n.kind, Kind) for n in _leaves(doc))


_DIRECTIVE_NAMES = st.sampled_from(["include", "includeEtc", "remove", "calc", "neg", "a"])


@settings(max_examples=300, deadline=None)
@given(
    chain=st.lists(st.tuples(_DIRECTIVE_NAMES, st.booleans()), min_size=1, max_size=150),
    as_entry=st.booleans(),
    data=st.data(),
)
def test_directives_nested_in_directives(
    chain: list[tuple[str, bool]], as_entry: bool, data: st.DataObject
) -> None:
    """A directive whose argument is a directive is refused; one inside brackets is read.

    Each level is ``#name argument`` or ``#name ( argument )``. Whatever the depth, the result
    is a tree or a typed error, never a RecursionError.
    """

    def gap() -> str:
        return data.draw(st.sampled_from(_GAPS))

    text = '"x"'
    for name, bracketed in reversed(chain):
        text = f"#{name}{gap()}({gap()}{text}{gap()})" if bracketed else f"#{name}{gap()}{text}"
    if as_entry:
        text = f"k {text};"
    source = text.encode("ascii")
    chained = any(not bracketed for _, bracketed in chain[:-1])
    too_deep = sum(bracketed for _, bracketed in chain) > MAX_DEPTH
    try:
        doc = parse(source)
    except StokesError as err:
        assert chained or too_deep, err.message
        assert err.code in ("foam.read.syntax", "foam.read.limit")
        return
    assert not chained and not too_deep
    assert serialise(doc) == source


def _leaves(node: Node) -> list[Token]:
    if isinstance(node, Token):
        return [node]
    return [leaf for part in node.parts for leaf in _leaves(part)]
