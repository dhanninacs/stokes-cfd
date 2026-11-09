"""A span-preserving reader for OpenFOAM 14 dictionaries (ADR-0029, plan T-303).

## Responsibility

`parse` turns the bytes of one dictionary file into a concrete syntax tree whose every node
records its byte span in the source, and `serialise` turns a tree back into bytes. **With no
edits the round trip is byte-identical**, comments, whitespace, banner and ``FoamFile`` header
included: an imported case's unedited files pass through untouched (ADR-0029 decision 4), so
this reader exists for the ``external`` lint profile and for overrides only, never to
re-format. Writing new dictionaries stays with `stokes.compile.foam`.

## A security boundary

The input is a user's file and is untrusted (CLAUDE.md §4). The limits are checked before the
tree is built: at most `MAX_BYTES` of input, at most `MAX_TOKENS` tokens (trivia included, so
memory is bounded too) and brackets nested at most `MAX_DEPTH` deep. That cap, with the
rule that a directive's argument is never another directive, bounds the parser's recursion.
Every failure is a `FoamReadError`: `FoamReadType` (``foam.read.type``, the input is not
bytes), `FoamReadLimit` (``foam.read.limit``) or `FoamSyntaxError` (``foam.read.syntax``, with
line and column in ``details``). A message quotes at most `QUOTE_MAX` characters of the
input. Nothing here evaluates, expands or includes anything: ``$var``, ``#include`` and
``#{ ... #}`` code are recorded as nodes for the lint to judge, and no file but the one handed
in is ever read.

## Grammar, as read

A dictionary body is a sequence of entries (``key value ... ;``), blocks (``key { ... }``),
directives (``#name argument``) and stray ``;``. Values are words, numbers, quoted strings,
``$`` variables, ``#{ ... #}`` verbatim code, lists ``( ... )``, dimension sets ``[ ... ]``,
braced dictionaries and directives. A word may carry balanced parentheses (``div(phi,U)``), as
OpenFOAM's own reader allows. ``#includeFunc``-style and ``#if``-style directives take the
rest of their line; ``#else`` and ``#endif`` take nothing; every other directive takes one
value, which may not itself be a directive. A line argument ends at a ``//`` or ``/*`` outside
a quoted string. Bytes are read as Latin-1 so a character offset is a byte offset.

The module runs a little past 500 lines on purpose: the tokenizer, the limits and the parser are
one security boundary reviewed at T0, and one file keeps that review in one place.

Import rules: standard library and `stokes.errors` only. No Qt, VTK or OCP; headless.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from stokes.errors import StokesError

MAX_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 64
MAX_TOKENS = 1_000_000
QUOTE_MAX = 60

# Directives whose argument is the rest of the line: a function-object call may contain spaces
# (`patchAverage(name=inlet, fields=(p))`), and a condition is several tokens.
LINE_DIRECTIVES = frozenset({"includeFunc", "includeFunction", "includeModel", "if", "ifeq"})
NO_ARG_DIRECTIVES = frozenset({"else", "endif"})

_WS = " \t\n\r\f\v"
_WS_RE = re.compile(r"[ \t\n\r\f\v]+")
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_STRING_RE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"', re.DOTALL)
_DIRECTIVE_RE = re.compile(r"#[A-Za-z]+")
_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
# The run of characters a word or variable may hold before a parenthesis or a slash needs a look.
_CHUNK_RE = re.compile(r"[^ \t\n\r\f\v\"';{}()\[\]/\x00-\x1f\x7f]*")
# A line directive's argument: the rest of the line up to a `//` or `/*` that is not inside a
# quoted string (backslash escapes honoured). Possessive quantifiers keep the match linear.
_LINE_ARG_RE = re.compile(r'(?:[^"/\n]++|"(?:[^"\\\n]|\\[^\n])*+"?|/(?![/*]))*+')
_DELIMITERS = frozenset(_WS + ';{}()[]"')
_OPEN = {"{": "}", "(": ")", "[": "]"}


class FoamReadError(StokesError):
    """A dictionary could not be read. The base for the reader's typed errors."""

    code = "foam.read"


class FoamReadLimit(FoamReadError):
    """The input exceeds a size, depth or token limit; nothing was parsed."""

    code = "foam.read.limit"


class FoamReadType(FoamReadError):
    """The input is not bytes, a bytearray or a memoryview; nothing was read."""

    code = "foam.read.type"


class FoamSyntaxError(FoamReadError):
    """The input is not a dictionary this reader understands; ``details`` say where."""

    code = "foam.read.syntax"


class Kind(StrEnum):
    """What a leaf token is. ``WS`` and ``COMMENT`` are trivia; ``LINE`` is a directive's line."""

    WS = "ws"
    COMMENT = "comment"
    WORD = "word"
    NUMBER = "number"
    STRING = "string"
    VARIABLE = "variable"
    DIRECTIVE = "directive"
    VERBATIM = "verbatim"
    LINE = "line"
    PUNCT = "punct"


_TRIVIA = frozenset({Kind.WS, Kind.COMMENT})
_KEY_KINDS = frozenset({Kind.WORD, Kind.STRING, Kind.VARIABLE, Kind.NUMBER})
_LEAF_VALUES = frozenset({Kind.WORD, Kind.NUMBER, Kind.STRING, Kind.VARIABLE, Kind.VERBATIM})


@dataclass(frozen=True, slots=True)
class Token:
    """One leaf: its kind, its exact source text and the byte offset it starts at."""

    kind: Kind
    text: str
    start: int

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    @property
    def is_trivia(self) -> bool:
        return self.kind in _TRIVIA


@dataclass(frozen=True, slots=True)
class _Group:
    """A node made of parts, in source order, trivia and punctuation included."""

    parts: tuple[Node, ...]
    start: int
    end: int

    @property
    def items(self) -> tuple[Node, ...]:
        """The significant parts: no trivia, no brackets, no semicolons."""
        return tuple(
            p
            for p in self.parts
            if not (isinstance(p, Token) and (p.is_trivia or p.kind is Kind.PUNCT))
        )


@dataclass(frozen=True, slots=True)
class Document(_Group):
    """A whole file: the top-level dictionary body, with no braces."""


@dataclass(frozen=True, slots=True)
class DictNode(_Group):
    """A braced dictionary ``{ ... }``."""


@dataclass(frozen=True, slots=True)
class ListNode(_Group):
    """A list ``( ... )``. A preceding size (``3(1 2 3)``) is a separate number value."""


@dataclass(frozen=True, slots=True)
class DimsNode(_Group):
    """A dimension set ``[0 1 -1 0 0 0 0]`` or ``[m/s]``."""


@dataclass(frozen=True, slots=True)
class _Keyed(_Group):
    """A node whose first part is its key token."""

    @property
    def key(self) -> Token:
        first = self.parts[0]
        assert isinstance(first, Token)
        return first


@dataclass(frozen=True, slots=True)
class EntryNode(_Keyed):
    """``key value ... ;``. ``items`` are the key followed by the values."""

    @property
    def values(self) -> tuple[Node, ...]:
        return self.items[1:]


@dataclass(frozen=True, slots=True)
class BlockNode(_Keyed):
    """``key { ... }``: a named sub-dictionary."""

    @property
    def body(self) -> DictNode:
        last = self.parts[-1]
        assert isinstance(last, DictNode)
        return last


@dataclass(frozen=True, slots=True)
class DirectiveNode(_Group):
    """``#name argument``. The argument is a value, a ``LINE`` token, or absent."""

    @property
    def name(self) -> str:
        head = self.parts[0]
        assert isinstance(head, Token)
        return head.text[1:]

    @property
    def argument(self) -> Node | None:
        rest = self.items[1:]
        return rest[0] if rest else None


type Node = (
    Token | Document | DictNode | ListNode | DimsNode | EntryNode | BlockNode | DirectiveNode
)


def _position(text: str, offset: int) -> dict[str, int]:
    """1-based line and column of a byte offset, for an error a user can find."""
    line = text.count("\n", 0, offset) + 1
    column = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return {"line": line, "column": column, "offset": offset}


def _quote(text: str) -> str:
    """``text`` quoted for an error message, cut to at most `QUOTE_MAX` characters plus ``...``.

    Messages reach the UI, CLI, HTTP and MCP callers and the logs, and CLAUDE.md section 4
    forbids logging file contents: an 8 MiB token must not become an 8 MiB message. The input
    is sliced before ``repr`` so the cost stays bounded, and the ``repr`` itself is cut because
    the escapes of non-printable bytes can make it four times longer than the slice.
    """
    shown = repr(text[:QUOTE_MAX])
    if len(text) > QUOTE_MAX or len(shown) > QUOTE_MAX:
        shown = shown[:QUOTE_MAX] + "..."
    return shown


def _syntax(text: str, offset: int, what: str) -> FoamSyntaxError:
    where = _position(text, offset)
    return FoamSyntaxError(
        f"{what} at line {where['line']}, column {where['column']}", details=where
    )


def _scan_word(text: str, pos: int) -> int:
    """The end of a word starting at ``pos``: chunks joined by balanced ``( )`` and lone ``/``."""
    depth = 0
    i = pos
    n = len(text)
    while True:
        match = _CHUNK_RE.match(text, i)
        assert match is not None  # the pattern matches the empty string
        i = match.end()
        if i >= n:
            return i
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")" and depth > 0:
            depth -= 1
        elif c != "/" or text.startswith(("//", "/*"), i):
            return i
        i += 1


class _Tokenizer:
    """Turns text into tokens, counting them and the bracket depth as it goes."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens: list[Token] = []
        self.depth = 0

    def emit(self, kind: Kind, start: int, end: int) -> int:
        self.tokens.append(Token(kind, self.text[start:end], start))
        if len(self.tokens) > MAX_TOKENS:
            raise FoamReadLimit(
                f"the dictionary has more than {MAX_TOKENS} tokens",
                details={"limit": "tokens", "maximum": MAX_TOKENS},
            )
        return end

    def run(self) -> list[Token]:
        text = self.text
        pos = 0
        n = len(text)
        while pos < n:
            pos = self.step(pos, text[pos])
        return self.tokens

    def step(self, pos: int, c: str) -> int:  # noqa: PLR0911 - one branch per token shape
        text = self.text
        if c in _WS:
            return self.emit(Kind.WS, pos, _match_end(_WS_RE, text, pos))
        if text.startswith("//", pos):
            return self.emit(Kind.COMMENT, pos, _match_end(_LINE_COMMENT_RE, text, pos))
        if text.startswith("/*", pos):
            close = text.find("*/", pos + 2)
            if close < 0:
                raise _syntax(text, pos, "unterminated /* comment")
            return self.emit(Kind.COMMENT, pos, close + 2)
        if c == '"':
            match = _STRING_RE.match(text, pos)
            if match is None:
                raise _syntax(text, pos, "unterminated string")
            return self.emit(Kind.STRING, pos, match.end())
        if c in "{([":
            return self.open(pos)
        if c in "})]":
            self.depth = max(0, self.depth - 1)
            return self.emit(Kind.PUNCT, pos, pos + 1)
        if c == ";":
            return self.emit(Kind.PUNCT, pos, pos + 1)
        if c == "#":
            return self.hash(pos)
        if c == "$":
            return self.variable(pos)
        if c == "'" or ord(c) < 0x20 or c == "\x7f":
            raise _syntax(text, pos, f"unexpected character {c!r}")
        number = _NUMBER_RE.match(text, pos)
        if number is not None:
            after = number.end()
            if (
                after == len(text)
                or text[after] in _DELIMITERS
                or text.startswith(("//", "/*"), after)
            ):
                return self.emit(Kind.NUMBER, pos, after)
        return self.emit(Kind.WORD, pos, _scan_word(text, pos))

    def open(self, pos: int) -> int:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FoamReadLimit(
                f"brackets are nested deeper than {MAX_DEPTH} levels",
                details={"limit": "depth", "maximum": MAX_DEPTH, **_position(self.text, pos)},
            )
        return self.emit(Kind.PUNCT, pos, pos + 1)

    def hash(self, pos: int) -> int:
        text = self.text
        if text.startswith("#{", pos):
            close = text.find("#}", pos + 2)
            if close < 0:
                raise _syntax(text, pos, "unterminated #{ code block")
            return self.emit(Kind.VERBATIM, pos, close + 2)
        match = _DIRECTIVE_RE.match(text, pos)
        if match is None:
            raise _syntax(text, pos, "'#' is not followed by a directive name")
        end = self.emit(Kind.DIRECTIVE, pos, match.end())
        if text[pos + 1 : end] not in LINE_DIRECTIVES:
            return end
        # The rest of the line, up to a comment outside a string, is one argument; its trailing
        # blanks are trivia.
        gap = end
        while gap < len(text) and text[gap] in " \t":
            gap += 1
        if gap > end:
            end = self.emit(Kind.WS, end, gap)
        line_end = _match_end(_LINE_ARG_RE, text, gap)
        arg_end = len(text[gap:line_end].rstrip(_WS)) + gap
        if arg_end > gap:
            end = self.emit(Kind.LINE, gap, arg_end)
        return end

    def variable(self, pos: int) -> int:
        text = self.text
        if text.startswith("${", pos):
            close = text.find("}", pos + 2)
            if close < 0:
                raise _syntax(text, pos, "unterminated ${ variable")
            return self.emit(Kind.VARIABLE, pos, close + 1)
        end = _scan_word(text, pos + 1)
        if end == pos + 1:
            raise _syntax(text, pos, "'$' is not followed by a name")
        return self.emit(Kind.VARIABLE, pos, end)


def _match_end(pattern: re.Pattern[str], text: str, pos: int) -> int:
    match = pattern.match(text, pos)
    assert match is not None  # callers only use patterns that match where they look
    return match.end()


class _Parser:
    """Recursive descent over the token list.

    Recursion is bounded by two rules together: the tokenizer caps bracket nesting at
    `MAX_DEPTH`, and `directive` refuses a directive as another directive's argument, so a
    chain such as ``#include #include ... x`` cannot recurse without a bracket in between.
    """

    def __init__(self, text: str, tokens: list[Token]) -> None:
        self.text = text
        self.tokens = tokens
        self.i = 0

    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def trivia(self, parts: list[Node]) -> Token | None:
        """Move trivia into ``parts``; return the next significant token, if any."""
        while (token := self.peek()) is not None and token.is_trivia:
            parts.append(self.take())
        return token

    def error(self, token: Token | None, what: str) -> FoamSyntaxError:
        offset = len(self.text) if token is None else token.start
        return _syntax(self.text, offset, what)

    def body(self, braced: bool) -> list[Node]:
        parts: list[Node] = []
        while True:
            token = self.trivia(parts)
            if token is None:
                if braced:
                    raise self.error(None, "unclosed '{'")
                return parts
            if token.text == "}" and token.kind is Kind.PUNCT:
                if braced:
                    return parts
                raise self.error(token, "unexpected '}'")
            if token.kind is Kind.PUNCT and token.text == ";":
                parts.append(self.take())
            elif token.kind is Kind.DIRECTIVE:
                parts.append(self.directive())
            elif token.kind in _KEY_KINDS:
                parts.append(self.entry())
            else:
                raise self.error(token, f"unexpected {_quote(token.text)} where a keyword belongs")

    def entry(self) -> EntryNode | BlockNode:
        key = self.take()
        parts: list[Node] = [key]
        token = self.trivia(parts)
        if token is not None and token.kind is Kind.PUNCT and token.text == "{":
            block = self.braced()
            parts.append(block)
            return BlockNode(tuple(parts), key.start, block.end)
        while True:
            token = self.trivia(parts)
            if token is None or (token.kind is Kind.PUNCT and token.text == "}"):
                raise self.error(token, f"the entry {_quote(key.text)} is missing its ';'")
            if token.kind is Kind.PUNCT and token.text == ";":
                parts.append(self.take())
                return EntryNode(tuple(parts), key.start, token.end)
            parts.append(self.value())

    def value(self) -> Node:
        token = self.peek()
        assert token is not None  # callers check for the end first
        if token.kind in _LEAF_VALUES:
            return self.take()
        if token.kind is Kind.DIRECTIVE:
            return self.directive()
        if token.kind is Kind.PUNCT and token.text == "{":
            return self.braced()
        if token.kind is Kind.PUNCT and token.text in "([":
            return self.bracketed()
        raise self.error(token, f"unexpected {_quote(token.text)} where a value belongs")

    def braced(self) -> DictNode:
        opener = self.take()
        parts: list[Node] = [opener, *self.body(braced=True)]
        closer = self.take()
        parts.append(closer)
        return DictNode(tuple(parts), opener.start, closer.end)

    def bracketed(self) -> ListNode | DimsNode:
        opener = self.take()
        closing = _OPEN[opener.text]
        dims = opener.text == "["
        parts: list[Node] = [opener]
        while True:
            token = self.trivia(parts)
            if token is None:
                raise self.error(None, f"unclosed {opener.text!r}")
            if token.kind is Kind.PUNCT and token.text == closing:
                parts.append(self.take())
                node = DimsNode if dims else ListNode
                return node(tuple(parts), opener.start, token.end)
            if dims and token.kind not in (Kind.WORD, Kind.NUMBER, Kind.VARIABLE):
                raise self.error(token, f"unexpected {_quote(token.text)} in a dimension set")
            parts.append(self.value())

    def directive(self) -> DirectiveNode:
        head = self.take()
        parts: list[Node] = [head]
        name = head.text[1:]
        if name in NO_ARG_DIRECTIVES:
            return DirectiveNode((head,), head.start, head.end)
        if name in LINE_DIRECTIVES:
            token = self.trivia(parts)
            if token is None or token.kind is not Kind.LINE:
                raise self.error(head, f"{_quote(head.text)} needs an argument on its line")
            parts.append(self.take())
            return DirectiveNode(tuple(parts), head.start, token.end)
        token = self.trivia(parts)
        if token is None or (token.kind is Kind.PUNCT and token.text in ";})]"):
            raise self.error(head, f"{_quote(head.text)} needs an argument")
        if token.kind is Kind.DIRECTIVE:
            # Unbounded otherwise: each chained directive is a recursion level without a bracket.
            raise self.error(token, f"{_quote(head.text)} cannot take a directive as its argument")
        argument = self.value()
        parts.append(argument)
        return DirectiveNode(tuple(parts), head.start, argument.end)


def parse(data: bytes | bytearray | memoryview) -> Document:
    """The tree of one dictionary file.

    Raises `FoamReadType`, `FoamReadLimit` or `FoamSyntaxError` only.
    """
    if not isinstance(data, bytes | bytearray | memoryview):
        raise FoamReadType(
            f"a dictionary is read from bytes, not {type(data).__name__}",
            details={"type": type(data).__name__},
        )
    size = memoryview(data).nbytes  # bytes, not items, for a wide-format memoryview
    if size > MAX_BYTES:
        raise FoamReadLimit(
            f"the dictionary is larger than {MAX_BYTES} bytes",
            details={"limit": "bytes", "maximum": MAX_BYTES, "size": size},
        )
    text = bytes(data).decode("latin-1")
    tokenizer = _Tokenizer(text)
    tokens = tokenizer.run()
    parser = _Parser(text, tokens)
    parts = parser.body(braced=False)
    return Document(tuple(parts), 0, len(text))


def _pieces(node: Node, out: list[str]) -> None:
    if isinstance(node, Token):
        out.append(node.text)
        return
    for part in node.parts:
        _pieces(part, out)


def source_text(node: Node) -> str:
    """The node's text as it reads in its file (Latin-1, one character per byte)."""
    out: list[str] = []
    _pieces(node, out)
    return "".join(out)


def serialise(node: Node) -> bytes:
    """The bytes of a node: the input itself for an unedited tree."""
    try:
        return source_text(node).encode("latin-1")
    except UnicodeEncodeError as exc:
        raise FoamReadError(
            "a node holds a character that is not one byte", details={"reason": str(exc)}
        ) from exc


def walk(node: Node) -> Iterator[Node]:
    """Every node under ``node``, itself first, in source order. For the lint."""
    stack: list[Node] = [node]
    while stack:
        current = stack.pop()
        yield current
        if not isinstance(current, Token):
            stack.extend(reversed(current.parts))
