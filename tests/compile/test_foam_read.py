"""Unit tests for the span-preserving dictionary reader (ADR-0029, T-303).

Every dictionary here is synthetic and written in this file; nothing is read from a tutorial.
"""

from __future__ import annotations

import pytest

from stokes.compile import foam_read
from stokes.compile.foam_read import (
    BlockNode,
    DictNode,
    DimsNode,
    DirectiveNode,
    Document,
    EntryNode,
    FoamReadError,
    FoamReadLimit,
    FoamReadType,
    FoamSyntaxError,
    Kind,
    ListNode,
    Token,
    parse,
    serialise,
    source_text,
    walk,
)
from stokes.errors import StokesError

SAMPLE = b"""/*--------------------------------*- C++ -*----------------------------------*\\
  a synthetic banner
\\*---------------------------------------------------------------------------*/
FoamFile
{
    format      ascii;
    class       dictionary;
    object      sampleDict;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

viscosity       [0 2 -1 0 0 0 0] 1.5e-05;
speed           [m/s] 2;
mode            on;   // trailing comment
label           "a quoted string";
grad(alpha,beta) Gauss linear;
"(left|right)"
{
    $base;
    tolerance   1e-06;
    nested      { depth 2; }
}
#include        "extras/partA"
#includeEtc     "caseDicts/thing"
#includeFunc    sampleAverage(name=front, fields=(a b))  // two fields
#remove         spare
offsets         3(1 -2 .5);
patches
(
    front { kind wall; }
    back  { kind wall; }
);
code            #calc "$a * 2";
${scoped.name}  4;
;
"""


def _only(doc: Document, index: int) -> foam_read.Node:
    return doc.items[index]


# ---------------------------------------------------------------- round trip and structure


def test_sample_round_trips_byte_for_byte() -> None:
    assert serialise(parse(SAMPLE)) == SAMPLE


def test_empty_and_trivia_only_inputs() -> None:
    assert parse(b"").parts == ()
    doc = parse(b"  // only a comment\n/* and a block */\n")
    assert doc.items == ()
    assert all(isinstance(p, Token) and p.is_trivia for p in doc.parts)
    assert serialise(doc) == b"  // only a comment\n/* and a block */\n"


def test_crlf_tabs_and_non_ascii_comment_round_trip() -> None:
    data = "a\t1;\r\n// d\u00e9j\u00e0 vu\r\nb 2;\r\n".encode()
    assert serialise(parse(data)) == data


def test_spans_are_byte_offsets() -> None:
    data = "// \u00e9\u00e9\nkey value;\n".encode()
    entry = parse(data).items[0]
    assert isinstance(entry, EntryNode)
    assert data[entry.start : entry.end] == b"key value;"
    for node in walk(parse(data)):
        assert serialise(node) == data[node.start : node.end]


def test_every_span_matches_its_text() -> None:
    doc = parse(SAMPLE)
    for node in walk(doc):
        assert SAMPLE[node.start : node.end] == serialise(node)


def test_header_is_an_ordinary_block() -> None:
    doc = parse(SAMPLE)
    header = doc.items[0]
    assert isinstance(header, BlockNode)
    assert header.key.text == "FoamFile"
    keys = [n.key.text for n in header.body.items if isinstance(n, EntryNode)]
    assert keys == ["format", "class", "object"]


def test_entry_values_and_kinds() -> None:
    doc = parse(SAMPLE)
    by_key = {n.key.text: n for n in doc.items if isinstance(n, EntryNode | BlockNode)}
    visc = by_key["viscosity"]
    assert isinstance(visc, EntryNode)
    dims, number = visc.values
    assert isinstance(dims, DimsNode)
    exponents = [t.text for t in dims.items if isinstance(t, Token)]
    assert exponents == ["0", "2", "-1", "0", "0", "0", "0"]
    assert isinstance(number, Token) and number.kind is Kind.NUMBER and number.text == "1.5e-05"
    speed = by_key["speed"]
    assert isinstance(speed, EntryNode)
    assert source_text(speed.values[0]) == "[m/s]"
    label = by_key["label"]
    assert isinstance(label, EntryNode)
    assert label.values[0] == Token(Kind.STRING, '"a quoted string"', label.values[0].start)
    grad = by_key["grad(alpha,beta)"]
    assert isinstance(grad, EntryNode) and grad.key.kind is Kind.WORD
    assert [v.text for v in grad.values if isinstance(v, Token)] == ["Gauss", "linear"]
    scoped = by_key["${scoped.name}"]
    assert isinstance(scoped, EntryNode) and scoped.key.kind is Kind.VARIABLE


def test_nested_block_and_variable_entry() -> None:
    doc = parse(SAMPLE)
    block = next(n for n in doc.items if isinstance(n, BlockNode) and n.key.text.startswith('"'))
    assert block.key.kind is Kind.STRING
    first = block.body.items[0]
    assert isinstance(first, EntryNode) and first.key.text == "$base" and first.values == ()
    nested = block.body.items[2]
    assert isinstance(nested, BlockNode)
    assert source_text(nested.body) == "{ depth 2; }"


def test_directives() -> None:
    doc = parse(SAMPLE)
    directives = [n for n in doc.items if isinstance(n, DirectiveNode)]
    names = [d.name for d in directives]
    assert names == ["include", "includeEtc", "includeFunc", "remove"]
    args = [d.argument for d in directives]
    assert all(isinstance(a, Token) for a in args)
    texts = [a.text for a in args if isinstance(a, Token)]
    assert texts == [
        '"extras/partA"',
        '"caseDicts/thing"',
        "sampleAverage(name=front, fields=(a b))",
        "spare",
    ]
    func = args[2]
    assert isinstance(func, Token) and func.kind is Kind.LINE


def test_directive_as_a_value_and_code_block() -> None:
    doc = parse(b'v #codeStream { code #{ int x = 1; } #}; };\nw #calc "1+2";\n')
    v, w = doc.items
    assert isinstance(v, EntryNode)
    stream = v.values[0]
    assert isinstance(stream, DirectiveNode) and stream.name == "codeStream"
    body = stream.argument
    assert isinstance(body, DictNode)
    code = body.items[0]
    assert isinstance(code, EntryNode)
    verbatim = code.values[0]
    assert isinstance(verbatim, Token) and verbatim.kind is Kind.VERBATIM
    assert verbatim.text == "#{ int x = 1; } #}"
    assert isinstance(w, EntryNode)
    calc = w.values[0]
    assert isinstance(calc, DirectiveNode) and calc.name == "calc"


def test_conditional_directives() -> None:
    data = b"#ifeq $a on\nx 1;\n#else\nx 2;\n#endif\n#includeFunc\tf()\t\n"
    doc = parse(data)
    names = [n.name for n in doc.items if isinstance(n, DirectiveNode)]
    assert names == ["ifeq", "else", "endif", "includeFunc"]
    first = doc.items[0]
    assert isinstance(first, DirectiveNode) and isinstance(first.argument, Token)
    assert first.argument.text == "$a on"
    else_ = doc.items[2]
    assert isinstance(else_, DirectiveNode) and else_.argument is None
    assert serialise(doc) == data


def test_line_directive_stops_at_comments() -> None:
    doc = parse(b"#includeFunc f(x) /* note */\n#includeFunc g() // note\n")
    args = [n.argument for n in doc.items if isinstance(n, DirectiveNode)]
    assert [a.text for a in args if isinstance(a, Token)] == ["f(x)", "g()"]


@pytest.mark.parametrize(
    ("data", "argument"),
    [
        (b'#includeFunc probes(file="a//b", fields=(p))', 'probes(file="a//b", fields=(p))'),
        (b'#includeFunc probes(x="/*")', 'probes(x="/*")'),
        (b'#includeFunc f(x="a\\"//b") // note\n', 'f(x="a\\"//b")'),
        (b'#includeFunc f(x="/*") /* note */\n', 'f(x="/*")'),
    ],
)
def test_line_directive_ignores_comment_markers_in_strings(data: bytes, argument: str) -> None:
    doc = parse(data)
    directive = doc.items[0]
    assert isinstance(directive, DirectiveNode)
    arg = directive.argument
    assert isinstance(arg, Token) and arg.kind is Kind.LINE
    assert arg.text == argument
    assert serialise(doc) == data


def test_lists_sized_lists_and_list_of_blocks() -> None:
    doc = parse(SAMPLE)
    entries = {n.key.text: n for n in doc.items if isinstance(n, EntryNode)}
    size, lst = entries["offsets"].values
    assert isinstance(size, Token) and size.kind is Kind.NUMBER and size.text == "3"
    assert isinstance(lst, ListNode)
    assert [t.text for t in lst.items if isinstance(t, Token)] == ["1", "-2", ".5"]
    patches = entries["patches"].values[0]
    assert isinstance(patches, ListNode)
    kinds = [type(i).__name__ for i in patches.items]
    assert kinds == ["Token", "DictNode", "Token", "DictNode"]


def test_nested_lists() -> None:
    doc = parse(b"pts ((0 0 0) (1 0 0) ((a)));")
    entry = doc.items[0]
    assert isinstance(entry, EntryNode)
    outer = entry.values[0]
    assert isinstance(outer, ListNode)
    assert len(outer.items) == 3 and all(isinstance(i, ListNode) for i in outer.items)


def test_token_kinds_individually() -> None:
    cases = {
        b"k word;": Kind.WORD,
        b"k -1.5e+3;": Kind.NUMBER,
        b'k "s \\" q";': Kind.STRING,
        b"k $v;": Kind.VARIABLE,
        b"k $:a.b;": Kind.VARIABLE,
        b"k $../x;": Kind.VARIABLE,
        b"k ${a.b};": Kind.VARIABLE,
        b"k #{ code #};": Kind.VERBATIM,
        b"k 1x;": Kind.WORD,
        b"k -;": Kind.WORD,
        b"k a/b;": Kind.WORD,
    }
    for data, kind in cases.items():
        entry = parse(data).items[0]
        assert isinstance(entry, EntryNode), data
        value = entry.values[0]
        assert isinstance(value, Token) and value.kind is kind, data
        assert serialise(parse(data)) == data


def test_word_stops_at_comment_and_semicolon() -> None:
    doc = parse(b"k a//c\n;\nm b/*c*/;")
    k, m = doc.items
    assert isinstance(k, EntryNode) and isinstance(m, EntryNode)
    assert [v.text for v in k.values if isinstance(v, Token)] == ["a"]
    assert [v.text for v in m.values if isinstance(v, Token)] == ["b"]
    assert parse(b"k 1//c\n;").items[0] == parse(b"k 1//c\n;").items[0]
    one = parse(b"k 1/*c*/;").items[0]
    assert isinstance(one, EntryNode)
    first = one.values[0]
    assert isinstance(first, Token) and first.kind is Kind.NUMBER


def test_unbalanced_word_paren_ends_at_whitespace() -> None:
    entry = parse(b"k f(a b;").items[0]
    assert isinstance(entry, EntryNode)
    assert [v.text for v in entry.values if isinstance(v, Token)] == ["f(a", "b"]
    with pytest.raises(FoamSyntaxError):
        parse(b"k f(a b);")


def test_stray_semicolons_are_kept() -> None:
    doc = parse(b";;a 1;;")
    assert serialise(doc) == b";;a 1;;"
    assert len(doc.items) == 1


def test_walk_visits_every_leaf_in_order() -> None:
    doc = parse(SAMPLE)
    leaves = [n.text for n in walk(doc) if isinstance(n, Token)]
    assert "".join(leaves).encode("latin-1") == SAMPLE
    assert next(iter(walk(doc))) is doc


def test_serialise_refuses_wide_characters() -> None:
    with pytest.raises(FoamReadError) as err:
        serialise(Token(Kind.WORD, "\u0101", 0))
    assert err.value.code == "foam.read"


# ---------------------------------------------------------------- limits


def test_size_limit_is_checked_before_parsing() -> None:
    with pytest.raises(FoamReadLimit) as err:
        parse(b"(" * (foam_read.MAX_BYTES + 1))
    assert err.value.code == "foam.read.limit"
    assert err.value.details["limit"] == "bytes"


def test_size_limit_boundary_is_inclusive() -> None:
    data = b" " * foam_read.MAX_BYTES
    assert serialise(parse(data)) == data


def test_depth_limit() -> None:
    ok = b"k " + b"(" * 64 + b")" * 64 + b";"
    assert serialise(parse(ok)) == ok
    deep = b"k " + b"(" * 65 + b")" * 65 + b";"
    with pytest.raises(FoamReadLimit) as err:
        parse(deep)
    assert err.value.code == "foam.read.limit"
    assert err.value.details["limit"] == "depth"


def test_depth_counts_mixed_brackets() -> None:
    deep = b"k " + b"{a " * 33 + b"(" * 32 + b"x"
    with pytest.raises(FoamReadLimit):
        parse(deep)


@pytest.mark.parametrize(
    "data",
    [
        b"#include " * 2000 + b"x",
        b"a " + b"#remove " * 2000 + b"x;",
        b"#a" * 1500 + b" x",
        b"#include #include x",
        b"k #calc #calc 1;",
    ],
)
def test_chained_directives_are_refused_not_recursed(data: bytes) -> None:
    with pytest.raises(FoamSyntaxError) as err:
        parse(data)
    assert err.value.code == "foam.read.syntax"
    assert "cannot take a directive" in err.value.message


def test_directive_inside_a_bracketed_argument_is_allowed() -> None:
    data = b'k #remove ( #calc "1" );\n#codeStream { #include "x" }\n'
    doc = parse(data)
    assert serialise(doc) == data


def test_memoryview_input() -> None:
    data = b"a 1;    "
    doc = parse(memoryview(data))
    assert serialise(doc) == data
    wide = memoryview(data).cast("I")  # two 4-byte items, eight bytes
    assert serialise(parse(wide)) == data
    assert serialise(parse(bytearray(data))) == data


def test_memoryview_size_is_measured_in_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(foam_read, "MAX_BYTES", 7)
    with pytest.raises(FoamReadLimit) as err:
        parse(memoryview(b"a 1;    ").cast("I"))
    assert err.value.details["size"] == 8


def test_token_limit_real_value() -> None:
    data = b"a " * (foam_read.MAX_TOKENS // 2 + 1)
    with pytest.raises(FoamReadLimit) as err:
        parse(data)
    assert err.value.details["limit"] == "tokens"


def test_token_limit_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(foam_read, "MAX_TOKENS", 4)
    assert serialise(parse(b"a 1;")) == b"a 1;"  # a, ws, 1, ; = 4 tokens
    with pytest.raises(FoamReadLimit):
        parse(b"a 1; ")


# ---------------------------------------------------------------- malformed input


@pytest.mark.parametrize(
    ("data", "line", "column"),
    [
        (b"a 1", 1, 4),
        (b"a 1 }", 1, 5),
        (b"}", 1, 1),
        (b"a {", 1, 4),
        (b"a (1 2", 1, 7),
        (b"a [0 1", 1, 7),
        (b"a [0 (1)];", 1, 6),
        (b"a (1 ]);", 1, 6),
        (b"a (1;);", 1, 5),
        (b"(a);", 1, 1),
        (b"a\n  'x';", 2, 3),
        (b"a\n\x01;", 2, 1),
        (b'a "open;', 1, 3),
        (b"a /* open", 1, 3),
        (b"a #{ open", 1, 3),
        (b"a ${open;", 1, 3),
        (b"a $;", 1, 3),
        (b"a #1;", 1, 3),
        (b"#include", 1, 1),
        (b"#include ;", 1, 1),
        (b"#includeFunc\nf();", 1, 1),
        (b"#includeFunc // none\n", 1, 1),
        (b"a b ) ;", 1, 5),
    ],
)
def test_syntax_errors_carry_line_and_column(data: bytes, line: int, column: int) -> None:
    with pytest.raises(FoamSyntaxError) as err:
        parse(data)
    assert err.value.code == "foam.read.syntax"
    assert err.value.details["line"] == line, err.value.message
    assert err.value.details["column"] == column, err.value.message
    assert isinstance(err.value, StokesError)
    assert err.value.to_dict()["details"]["offset"] >= 0


def test_error_messages_name_the_entry() -> None:
    with pytest.raises(FoamSyntaxError, match="'speed' is missing its ';'"):
        parse(b"speed 2\n")


# ---------------------------------------------------------------- bounded messages and types

_HUGE = foam_read.MAX_BYTES - 16


@pytest.mark.parametrize(
    "data",
    [
        b"a" * _HUGE,  # one key with no ';'
        b"#{" + b"x" * _HUGE + b"#}",  # a code block where a keyword belongs
        b"#" + b"a" * _HUGE,  # a directive with an enormous name and no argument
        b"k #" + b"a" * _HUGE + b";",  # an enormous directive as a value, no argument
        b"\x80" * _HUGE,  # escapes would make the repr four times longer than the slice
        b'k [ "' + b"x" * _HUGE + b'" ];',  # a long string where a dimension belongs
    ],
    ids=["key", "verbatim", "directive", "value-directive", "escapes", "dimension"],
)
def test_error_messages_quote_a_bounded_slice_of_the_input(data: bytes) -> None:
    with pytest.raises(FoamSyntaxError) as err:
        parse(data)
    assert err.value.code == "foam.read.syntax"
    assert len(err.value.message) < 200, err.value.message[:300]
    assert len(str(err.value.to_dict())) < 400
    assert "..." in err.value.message


def test_short_tokens_are_quoted_whole() -> None:
    with pytest.raises(FoamSyntaxError) as err:
        parse(b"k (1 ;);")
    assert "';'" in err.value.message
    assert "..." not in err.value.message


@pytest.mark.parametrize("data", [None, "a 1;", 7])
def test_parse_refuses_a_non_bytes_input(data: object) -> None:
    with pytest.raises(FoamReadType) as err:
        parse(data)  # type: ignore[arg-type]
    assert err.value.code == "foam.read.type"
    assert isinstance(err.value, FoamReadError)
    assert isinstance(err.value, StokesError)
