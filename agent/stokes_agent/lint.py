"""The dictionary lint: what a staged OpenFOAM dictionary may not contain (ADR-0024 §1.8, T-202b).

## Why an OpenFOAM dictionary needs a security review

An OpenFOAM dictionary is **code**. It has `#codeStream` and `#calc`, which compile and evaluate
C++ or arithmetic at read time; `#include`, which pulls in another file, by absolute path if it
likes; `#includeEtc`, which reaches into the OpenFOAM installation; `systemCall`, which runs a shell
command; and `libs`, which loads a shared library. A case file that the desktop wrote is data
until the solver reads it, at which point it can execute.

So every file the agent stages is scanned **before a utility runs**, and the scan refuses rather
than sanitises: a file containing one of these is not "cleaned up", it is rejected, because a
dictionary that has been edited by a linter is a dictionary whose meaning is no longer the one the
user wrote.

## The three moments it runs

`create` (the initial files), `put(final=True)` (a chunked upload that just completed) and `start`
(the last chance before a process reads the case). `start` is not redundant: `copy_from` can bring
files in from an earlier job, and a file staged by an older agent must not bypass the current
rules.

## It is a scan of the WHOLE text, not of lines

The first version stripped a comment from each line and then matched line-anchored patterns. Each of
those was a way round it: `/**/ #codeStream` (the comment swallowed the rest of the line),
`"a//b"; #calc` (a `//` inside a string), an include in the middle of a line, anything after an
include on the same line, `t codedMixed; type $t;`. So the text is first reduced by a small state
machine (`strip_comments`: double-quoted strings with backslash escapes, `//` and `/* */`, the
latter across lines) and every rule then runs over what is left **anywhere in it**, strings
included: a string is data to a reader of the file but a macro and an include argument are read
out of one.

## What is deliberately allowed

`#includeFunc` with a **known function name**, and `#includeEtc` with one of two **exact** names
(ADR-0024 §1.8). Both appear in OpenFOAM's own tutorials — pitzDaily's `system/functions` is
nothing but ``#includeFunc patchAverage(patch=inlet, fields=(p U))`` — so refusing them outright
would refuse the install's own cases. A relative `#include` is allowed when it resolves inside the
job's own dictionary folders.

**`ALLOWED_INCLUDE_FUNC` holds the names from the install rather than from memory**:
`patchFlowRate` and `patchAverage` are what `$FOAM_ETC/caseDicts/functions/` actually provides.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

#: The most of a file that is scanned. A dictionary larger than this is refused rather than scanned
#: partially: the part that was not read is exactly where a directive could hide.
MAX_SCAN_BYTES = 8 * 1024 * 1024

#: The most a staged ASCII STL may be. **500 MiB, the owner's decision.**
#:
#: This is the only staged file allowed past `MAX_SCAN_BYTES`, and it is allowed because a CAD
#: surface does not fit the dictionary-sized budget: an ASCII STL is roughly 260 bytes per triangle,
#: so a 2 M-triangle part is about half a gigabyte. The limit is enforced by `stat`, not by reading.
MAX_STL_BYTES = 500 * 1024 * 1024

#: How much of a staged STL is held at once. **One block, whatever the file's size**, which is the
#: property that lets the limit be 500 MiB: the scan streams and keeps only a partial line between
#: blocks. 1 MiB is large enough that the block loop is not the cost and small enough that several
#: concurrent jobs do not matter.
STL_BLOCK_BYTES = 1024 * 1024

#: The longest line an STL may have. A `vertex` line is under a hundred characters; this cap exists
#: so a file of one enormous line cannot make the carried-over tail grow without bound.
STL_MAX_LINE_BYTES = 4096

#: The most findings one sampled STL reports, so a deliberately malformed file cannot fill a
#: response with noise.
MAX_STL_FINDINGS = 32

#: Where a staged STL may live and what it must be called. **One path, one suffix**, so the
#: exception cannot be reached by any other staged file.
GEOMETRY_PREFIX = "constant/geometry/"
STL_SUFFIX = ".stl"

#: A whole line of an ASCII STL, matched COMPLETELY.
#:
#: **`fullmatch`, not `match`**: a prefix test accepted `vertex 0 0 0  #codeStream` - a valid
#: keyword followed by a directive on the same line - one of the ways Opus's review got a
#: `#codeStream` into a file the agent would let OpenFOAM parse.
#:
#: The numeric form accepts what STL writers actually emit: signs, decimal points and exponents.
_STL_FLOAT = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
#: `solid <name>` and `endsolid <name>`: **the name is free text, not a word.**
#:
#: The STL specification lets the name be anything, and writers use it: a part number, a path, a
#: timestamp - and `#` characters appear in real names (`part#7 issue 12`). Restricting it to
#: `[A-Za-z0-9_.-]+` refused `endsolid part#7`, a valid file, which my own false-refusal test
#: caught. The name is DATA to snappy and can never be a directive, because `_is_unsafe_include`
#: refuses to let any dictionary include a staged surface at all.
#: A `solid`/`endsolid` name: free text, but **not the characters that start a directive or end a
#: statement**.
#:
#: Opus's probe, reproduced: `endsolid x ; #codeStream { code "evil"; }` passed the grammar, because
#: a name was `[^\r\n]*` and so swallowed a whole directive. OpenFOAM never reads an STL as a
#: dictionary by itself - but `$file!entry` DID reach one (measured), and a name carrying a
#: statement terminator is the same class of hazard. A real name is a part number, a path or a
#: timestamp; none of those needs `#`, `;`, `{`, `}` or `$`.
_STL_NAME = r"[^\r\n#;{}$]*"

#: A whole STL line may not contain `$` either (claude-win2's 2310): **an STL has no macros**, so a
#: `$` in one is an attempt to have the file read as something else. Checked on the line, not only
#: in the name, because a `$` could sit in a `vertex` position where the number grammar would
#: otherwise refuse it for the wrong reason - and the message should name the real problem.
_STL_LINE_RE = re.compile(
    r"(?:"
    rf"facet\s+normal\s+{_STL_FLOAT}\s+{_STL_FLOAT}\s+{_STL_FLOAT}"
    r"|outer\s+loop|endloop|endfacet"
    rf"|vertex\s+{_STL_FLOAT}\s+{_STL_FLOAT}\s+{_STL_FLOAT}"
    rf"|solid(?:\s+{_STL_NAME})?"
    rf"|endsolid(?:\s+{_STL_NAME})?"
    r")"
)

#: The file's first `solid` line, whose name may be free text (importers put anything there).
_STL_SOLID_RE = re.compile(rf"solid(?:\s+{_STL_NAME})?")

#: Every line of an ASCII STL begins with one of these or a number (`facet normal`, `vertex`,
#: `outer loop`, `endloop`, `endfacet`, `solid`, `endsolid`).
_STL_KEYWORDS = (
    "solid",
    "endsolid",
    "facet",
    "endfacet",
    "outer",
    "endloop",
    "vertex",
)

#: A line longer than this is refused. OpenFOAM has no line that long, and a file that does
#: is either corrupt or built to defeat a scan.
MAX_LINE_CHARS = 4096

#: The most findings one file reports, so a hostile file cannot make the message as big as itself.
MAX_FINDINGS = 200

#: The `#` directives this lint ALLOWS. **An allow-list, not a deny-list** (FIXES-C item 2).
#:
#: A deny-list names what is forbidden and lets everything else through — so `#includeFvModel`,
# : `#includeFvConstraint`, or any directive added to a future OpenFOAM passes unchecked, and
# the only
#: defence is that nobody thought of it. This is the other way round: a directive not named here is
#: **refused**, including one this version does not have and one that does not exist at all.
#:
#: **The list is what our own writers emit**, measured rather than remembered:
# : `grep -rhoE '#include[A-Za-z]*' stokes/compile/ stokes/mesh/drivers/` gives exactly
# `#include` and
# : `#includeFunc`. Nothing else has a reason to be in a case Stokes generated. `#includeEtc`
# is the one
#: exception and is allowed **for two exact names only** (ADR-0024 §1.8), because OpenFOAM's own
#: tutorials use it and a copied tutorial must lint.
#:
#: **What OpenFOAM 14 really knows**, measured with `foamDictionary -expand` on this install:
#: `include includeEtc includeFunc includeIfPresent codeStream remove inputMode print dump exit` are
#: known; `calc neg sinclude eval` are **not**, though their source exists. So the allow-list is not
# : "everything this version has" either — a directive being real is not a reason for our
# cases to use it.
#: `includeFvModel` is compiled into `libfiniteVolume.so` but unknown to the utility, which is why
#: neither the utility's list nor the source tree alone settles this; recorded in `.coordination/`.
# **Lower-cased at construction**, because the check compares against `name.lower()`: an entry
# with a
# capital in it - `includeModel` was the first - would be silently absent from the set and refused,
# which the corpus check caught and no unit test did.
ALLOWED_DIRECTIVES: frozenset[str] = frozenset(
    name.lower()
    for name in (
        "include",  # a relative file inside the job's own dictionary folders
        "includeetc",  # only the exact names in ALLOWED_INCLUDE_ETC
        "includefunc",  # only the names in ALLOWED_INCLUDE_FUNC
        # **`#print` with no argument, which OpenFOAM's own pitzDaily ships** in
        # `constant/momentumTransport`. It prints the dictionary it appears in and does nothing
        # else: no code, no file, no path. Found by the acceptance test that lints the real
        # tutorial, which is why that test exists - a lint that refuses the install's own cases
        # is a lint that refuses cases OpenFOAM works with. `#print` WITH an argument is a
        # different matter and is caught by the path rules below, since its argument is a name.
        "print",
        # **Benign directives OpenFOAM's own tutorials use**, added because the corpus check
        # found them refused: `#neg` negates the next token, `#ifeq`/`#else`/`#endif` are
        # dictionary conditionals, and `#includeModel` pulls in an fvModel (the family that
        # `#includeFvModel` belongs to). None runs code, reads a file outside the case or takes
        # a path; they are dictionary arithmetic and control flow, and a case copied from a
        # tutorial must lint.
        "neg",
        "ifeq",
        "else",
        "endif",
        # **`includeModel` stays but is CHECKED** - see `_include_model_finding`. Four of
        # OpenFOAM's own tutorials use it (`#includeModel gradP`), so removing it refuses the
        # install; leaving it unchecked admits a directive whose name and arguments are read
        # against the install exactly as `#includeFunc`'s are, so it needs the same two checks.
        "includeModel",
        # **`#if` is NOT here** (Opus review item 5). It was, and **no file in the whole
        # installation's tutorials uses it** - measured, 0 - while it evaluates its condition,
        # which is why `#calc` is refused. An untested directive that evaluates an expression is
        # exactly what an allow-list exists to keep out.
        #
        # **`#codeBlock` and `#endCodeBlock` are deliberately NOT here, and that is the whole
        # point of an
        # allow-list.** They bracket a C++ block: OpenFOAM's own `blockMeshDict` uses
        # `#calc "$<scalar>cylMax"` inside one - a C++ cast evaluated by the compiled block, which
        # is code
        # execution at dictionary-read time, exactly what `#codeStream` is refused for. A
        # deny-list would
        # have let them through because nobody named them.
    )
)
"""Every `#` directive a staged case may use, lower-case without the `#`."""

#: Why a directive is refused, for the messages that name the dangerous ones by hand.
REFUSED_DIRECTIVE_WHY: dict[str, str] = {
    "codestream": "compiles and runs C++ while the dictionary is read",
    "calc": "evaluates an expression while the dictionary is read",
    "eval": "evaluates an expression while the dictionary is read",
    "sinclude": "includes a file the sandbox has not checked",
    "includeifpresent": "includes a file the sandbox has not checked",
}
"""A reason for the directives worth naming; any other unknown one gets the generic refusal."""

#: Regex metacharacters OpenFOAM treats as making a quoted key a PATTERN.
#:
#: **`dictionary.C` puts every keyword whose `isPattern()` is true into `patternRegexps_`, and
#: `wordRe::isPattern` is `string::meta<regExp>`** - so a quoted key is regex-matched by the solver.
#: `"l[i]bs" ("evil.so");` therefore selects the `libs` entry and loads a shared library, while the
#: word rule below sees `l[i]bs` and passes it. That is FIXES-C item 3.
PATTERN_META = "[]()|.*+?^$\\"

#: The quoted keys our own writer emits that legitimately contain metacharacters. **Exact strings**,
# : not shapes: `({inner})` is built by the writer and `(U|k|epsilon|f|v2)` is a field-group
# selection.
#: Any other pattern-looking key is refused, which is what makes this an allow-list rather than a
#: smarter deny-list.
ALLOWED_PATTERN_KEYS: frozenset[str] = frozenset(
    {
        # **`".*"` is what OUR OWN case writer emits**, in `relaxationFactors.equations`
        # beside a number
        # (`of14/__init__.py`: `Entry('".*"', 0.9)`) - the standard "relax everything else"
        # idiom. As a
        # regex it matches every entry name including `libs`, so the match test alone refused
        # it and the
        # **engine test** caught that by failing on our own generated `system/fvSolution:43`. No
        # unit test
        # and not the corpus - our own output. It is allowed by exact string, and a statement that
        # actually
        # does something with it (a quoted library path) is still caught by the word rules,
        # which see
        # whatever the value is.
        ".*",
        "({inner})",
        "(U|k|epsilon|f|v2)",
    }
)

#: A quoted key that is a **field-group selection**, which real `fvSolution` files use everywhere
# : (`"(p|rho).*"`). Such a key names no dictionary entry, so it cannot hide one; the rule
# below exists
#: for a key that can. Anything outside these two shapes - letters, digits, `_`, `.`, `|`, `(`, `)`,
#: `*` - is still refused, so `"l[i]bs"` is not exempt (it has brackets).
_FIELD_GROUP_RE = re.compile(r"^\.?\*?\(?[A-Za-z0-9_.|]*\)?[A-Za-z0-9_.*|]*$")

# : Function objects and keys that read or write a path of someone else's choosing.
# `timeActivatedFileUpdate`
# : with an absolute `fileToUpdate` is an arbitrary file write as the agent user, and it needs
# no library.
PATH_WRITING_KEYS: frozenset[str] = frozenset(
    {
        # **Not `file`.** It is an ordinary OpenFOAM keyword - 2,303 real uses across the
        # tutorials, in
        # `blockMeshDict`, `snappyHexMeshDict` and everywhere else - so refusing it refuses
        # the install.
        # The corpus check found that; no unit test would have. Only the keys that actually choose
        # a path
        # TO WRITE are here.
        "timeactivatedfileupdate",
        "filetoupdate",
    }
)

#: Keywords that turn an entry into an ACTION: `systemCall` runs its `execute`, and the coded hooks
#: compile what their `code*` keys hold. Used to tell a hiding pattern key from a field selection.
ACTION_WORDS: frozenset[str] = frozenset({"execute"})

#: Words refused anywhere in the text, with the reason (whole words, case-insensitive).
REFUSED_WORDS: dict[str, str] = {
    "systemcall": "runs a shell command",
    "libs": "loads a shared library into the solver",
    "functionobjectlibs": "loads a shared library into the solver",
    "codedfixedvalue": "compiles C++ for a boundary condition",
    "coded": "compiles C++",
    "codeinclude": "adds C++ headers to a compiled block",
    "codeoptions": "adds compiler options to a compiled block",
    "codelibs": "links libraries into a compiled block",
}

#: Entry keywords whose value may not be a `$` expansion (lowercase). `t codedMixed; type $t;` would
#: otherwise name a coded type without the word ever following `type`.
DOLLAR_REFUSED_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "libs",
        "functionobjectlibs",
        "codestream",
        "code",
        "codeinclude",
        "codeoptions",
        "codelibs",
        "systemcall",
    }
)

#: Function names `#includeFunc` may use: what `$FOAM_ETC/caseDicts/functions/` provides for a patch
#: average and a patch flow rate, and nothing else. A name that is not here is refused, because
#: `#includeFunc` resolves against the OpenFOAM installation and a name is a file path
#: underneath it.
#:
#: `stopAtFile` (`caseDicts/functions/control/stopAtFile`) is the graceful Stop: it ends the run
#: and writes the current iteration when a file named after the function appears in the case
#: (`job.stop` creates it). It runs no command and loads only the install's own utility library.
#: `fieldAverage` (`caseDicts/functions/fields/fieldAverage`) writes the time averages a transient
#: run asks for; it reads fields and writes `<field>Mean`, nothing else.
ALLOWED_INCLUDE_FUNC: frozenset[str] = frozenset(
    {"patchAverage", "patchFlowRate", "stopAtFile", "fieldAverage"}
)

#: The `#includeEtc` arguments allowed: **exact names**, relative to the OpenFOAM `etc` directory
#: (ADR-0024 §1.8; a gate task may add one). A prefix would let `caseDicts/../../x` through, and
#: nothing else under `etc` — `config.sh`, `bashrc` — is something a case has a reason to include.
ALLOWED_INCLUDE_ETC: tuple[str, ...] = (
    "caseDicts/setConstraintTypes",
    "caseDicts/mesh/generation/meshQualityDict",
)

#: Folders the agent writes itself, whose files are not scanned: `artifacts/` (binary, written
#: by `extract`) at the top of the job, and the `log.*` files. A client cannot stage into either
#: (`jobpaths.check_staged_path`).
AGENT_OWN_PREFIXES: tuple[str, ...] = ("artifacts/",)
AGENT_OWN_LOG_PREFIX = "log"

#: `constant/polyMesh` is machine-written by the mesher and copied verbatim by `copy_from`, so a
#: file there that the CLIENT staged is checked only for a `#` at all (and is still subject to the
#: size, line and binary limits), and nothing may `#include` from under it.
#:
#: **A file the agent's own mesher wrote is different** (FIXES-B B5): a real mesh is far over the
#: size limit and is binary when written that way, and a refusal there would refuse every real
#: case. `lint_job` skips a file listed in its ``trusted_polymesh`` map, and only that: the map
#: comes from the job's record (`jobs.py` writes it from what a mesher step produced, or from a
#: `copy_from` of such a job), never from the wire.
POLYMESH_PREFIX = "constant/polyMesh/"

# A double-quoted string (a backslash escapes the next character; an unterminated one ends at the
# newline, as OpenFOAM's reader does), a `//` comment, a `/* */` comment (unterminated: to the end
# of the text), or a run of anything else. Every character is in exactly one alternative.
_REDUCE_RE = re.compile(
    r'(?P<string>"(?:\\.|[^"\\\n])*"?)'
    r"|(?P<line>//[^\n]*)"
    r"|(?P<block>/\*.*?(?:\*/|\Z))"
    # **OpenFOAM's verbatim string, `#{ ... #}`.** It must be matchable before `other` or its
    # `#{` is read as a `/*`-style block that swallows the rest of the file - the CRITICAL
    # bypass: `x #{ // #}; libs ("a.so");` left `libs` invisible to every rule below, because
    # the stripper had blanked from `#{` to the end.
    #
    # **Its CONTENTS ARE KEPT, not blanked.** OpenFOAM reads a verbatim string as code or as an
    # expression, so it can carry the same dangerous words a plain string can - the module docstring
    # already says a string is "read out of" one. Blanking it would trade one bypass for another.
    # An unclosed one runs to the end of the text and `_verbatim_findings` refuses it.
    r"|(?P<verbatim>#\{.*?(?:#\}|\Z))"
    # **`#` and `{` are excluded from `other` so the verbatim alternative can match.** They were
    # allowed, so `other` consumed `x #{ ` - including the `#{` - and the `//` inside the verbatim
    # string then blanked the rest of the file. The alternatives after `other` only ever see a
    # position `other` cannot consume, so a character that starts a special form must not be in it.
    r'|(?P<other>[^"/#]+|[/#])',
    re.DOTALL,
)
_NOT_NEWLINE_RE = re.compile(r"[^\n]")

_INCLUDE_RE = re.compile(r"#include(?P<kind>etc|func|ifpresent)?(?![A-Za-z0-9_])", re.IGNORECASE)
#: **`#includeModel` is its own form, not an `#include` with a kind.** `_INCLUDE_RE`'s lookahead is
# : `(?![A-Za-z0-9_])`, so `#includeModel` never matched it - and a directive on the
# allow-list with no
#: name check and no argument check is a hole the allow-list was supposed to close (Opus item 5).
# **`#includeConstraint` is the same form for `fvConstraints`** (OpenFOAM 14:
# `#includeConstraint limitPressure(minFactor=0.1)`), so it gets the same check. Only the external
# profile allows it (`lint_external.EXTERNAL_DIRECTIVES`); here it is checked wherever it appears.
_INCLUDE_MODEL_RE = re.compile(r"#include(?:Model|Constraint)(?![A-Za-z0-9_])", re.IGNORECASE)
_QUOTED_ARG_RE = re.compile(r'[ \t\r\n]*"(?P<target>(?:\\.|[^"\\\n])*)"', re.DOTALL)
_FUNC_NAME_RE = re.compile(r'\s*(?P<name>[^\s(;{}"]*)')
_SAFE_INCLUDE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*(/[A-Za-z0-9_][A-Za-z0-9_.\-]*)*$")
_WORD_ALTERNATIVES = "|".join(re.escape(word) for word in REFUSED_WORDS)
_WORDS_RE = re.compile(rf"(?<![A-Za-z0-9_])({_WORD_ALTERNATIVES})(?![A-Za-z0-9_])")
_TOKEN_RE = re.compile(r'(?P<string>"(?:\\.|[^"\\\n])*"?)|(?P<punct>[;{}])|(?P<word>[^\s;{}"]+)')
_HASH_RE = re.compile(r'#[^\s;{}"]*')


@dataclass(frozen=True)
class Finding:
    """One reason a file was refused.

    ``line`` is 1-based and ``token`` is what was found, so a message can say where to look — a
    refusal that only says "unsafe dictionary" leaves the user hunting through a case they did not
    write. ``line`` is 0 for a finding about the file as a whole.
    """

    file: str
    line: int
    token: str
    why: str

    def to_json(self) -> dict[str, object]:
        return {"file": self.file, "line": self.line, "token": self.token, "why": self.why}


#: A check that returns why something is refused, or None when it may stand.
Verdict = Callable[[str], "str | None"]


@dataclass(frozen=True)
class LintRules:
    """The parts of the lint a **profile** may change; everything else is shared and fixed.

    **Only the points that differ between profiles are here** (ADR-0029 decision 5): which `#`
    directives are known, how the three include forms are judged, which words are refused
    anywhere, and one extra pass. The comment stripper, the `$` macro allow-list, the quoted
    pattern-key rule, `coded*` values, path-writing keys and the line limits are not parameters,
    so no profile can relax them.

    The current profile (ADR-0024 §1.8) is `CURRENT_RULES`, and **`lint_text`, `lint_file` and
    `lint_job` take no rules argument**: a caller of the current entry points cannot reach another
    profile. The `external` profile builds its own in `lint_external.py` and calls
    `lint_with_rules` directly.
    """

    directives: frozenset[str]
    include_etc: Verdict
    include_func: Verdict
    #: ``(target, including file) -> why``.
    include: Callable[[str, str], str | None]
    #: Words refused anywhere in the text (lower-case) and why.
    words: Mapping[str, str]
    #: A further pass over the comment-stripped text, appending ``(offset, finding)``.
    further: Callable[[str, str, list[tuple[int, Finding]]], None] = lambda *_: None

    @property
    def words_re(self) -> re.Pattern[str]:
        """The whole-word regex for `words` (`re` caches the compiled form by its text)."""
        if self.words is REFUSED_WORDS:
            return _WORDS_RE
        alternatives = "|".join(re.escape(word) for word in self.words)
        return re.compile(rf"(?<![A-Za-z0-9_])({alternatives})(?![A-Za-z0-9_])")


def strip_comments(text: str) -> str:
    """``text`` with every comment blanked, the same length and the same line breaks.

    A small state machine, written as one regular expression: double-quoted strings (a backslash
    escapes the next character) are kept whole, so a ``//`` inside one is not a comment; ``//``
    runs to the end of the line; ``/* */`` may span lines and, unterminated, runs to the end of the
    text. A comment becomes spaces (its newlines are kept) so an offset or a line number in the
    result is the same in the original. **What is returned is what a reader of the file sees as
    code**, which is what the rules below are run on.
    """
    parts: list[str] = []
    for match in _REDUCE_RE.finditer(text):
        chunk = match.group()
        if match.lastgroup in ("line", "block"):
            chunk = _NOT_NEWLINE_RE.sub(" ", chunk)
        parts.append(chunk)
    return "".join(parts)


def _is_unsafe_include(target: str, path: str) -> str | None:
    """Why a quoted `#include` argument may not be read, or None when it may.

    Only a relative path of plain names that resolves inside the job's own dictionary folders:
    no `$` expansion (`$FOAM_CASE`), no `~`, no absolute path, no `..` anywhere, no hidden name,
    nothing under `constant/polyMesh` (which is machine-written and linted only for a `#`), and not
    a partial upload.
    """
    if "$" in target or "~" in target or "\\" in target:
        return "the path is expanded or escaped, so it is not a path inside the job"
    if target.startswith("/") or ".." in target:
        return "the path leaves the job folder"
    if _SAFE_INCLUDE_RE.match(target) is None:
        return "the path is not a plain relative path of plain names"
    if target.endswith((".part", ".tmp")):
        return "the file is a partial upload"
    # **One check over both spellings of the target**: as written, and as it resolves from the
    # including file. A rule that only read the raw text missed `../constant/geometry/x.stl`.
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    return _reserved_include(target) or _reserved_include(resolved)


def _reserved_include(candidate: str) -> str | None:
    """Why a resolved include target may not be read, or None when it may.

    Two folders are reserved, and both are checked on a **folded** path:

    * `constant/polyMesh` is machine-written, so including it would feed the parser a file the
      agent wrote rather than one a user chose;
    * **a staged surface** - anything under `constant/geometry/`, or any `.stl` (Opus F1).
      OpenFOAM reads an INCLUDED file with the DICTIONARY parser whatever its extension, so a
      `#codeStream`/`#calc` hidden inside a staged STL executed once a dictionary included it. The
      include rule waved it through because `geometry` is not `polyMesh` and `.stl` is not `.part`:
      measured, `#include "geometry/part.stl"` was ACCEPTED, and so were `"geometry/PART.STL"` and
      `"geometry/part.stl."`.

    **Folded before it is judged**, because a filesystem is more forgiving than a string
    comparison: Windows is case-insensitive, drops a trailing dot or space, and reads `name:stream`
    as an alternate data stream on the same file. Judging the raw text left every one of those open.
    """
    # **Folded components**, so `geometry.` and `polyMesh:s` (which Windows reads as the folder
    # itself) are the reserved names they are (found while adding the external profile). This is
    # the one behaviour change in the current profile: `#include "geometry./x"` was accepted before
    # and is refused now; pinned by `test_current_profile_refuses_trailing_dot_reserved_folder`.
    for part in _fold_include(candidate).split("/"):
        why = _RESERVED_INCLUDE_PARTS.get(part)
        if why is not None:
            return why
    if _fold_include(candidate).endswith(".stl"):
        return _SURFACE_INCLUDE
    return None


#: A component that may never appear in an include target, and why.
_RESERVED_INCLUDE_PARTS = {
    "polymesh": "constant/polyMesh is machine-written and may not be included",
    "geometry": "a staged surface is data for snappy, not a dictionary to include",
}

#: The same refusal for a surface named outside `geometry/`, since the suffix decides too.
_SURFACE_INCLUDE = "a staged surface is data for snappy, not a dictionary to include"


#: The ONLY `$` forms a dictionary may contain. **An allow-list, not a list of refusals.**
#:
#: Opus's R3: my first rule was a text pattern guessing at danger, and it was wrong in both
#: directions - it refused 37 legitimate self-references and missed `$/etc/passwd!a`. An allow-list
#: inverts the question: a `$` is permitted when it is a plain entry reference, and EVERY other form
#: is refused, including forms nobody has thought of yet.
#:
#: **The spelling matrix, run on real OF14 with `foamDictionary -expand`** (never in a project):
#:
#: | spelling | OF14 result |
#: |---|---|
#: | `$target`, `${target}` | reads the entry - ALLOWED |
#: | `$sub/inner`, `${sub/inner}`, `$!sub/inner` | reads a SUB-ENTRY - ALLOWED |
#: | `$constant/geometry/part.stl!secretEntry` | **reads a FILE** -
#:   `includedDictionary::includedDictionary` |
#: | `$/etc/hostname` | resolves, then fails as an unknown ENTRY - a real path reach |
#: | `$:a.b`, `$..a`, `$.a`, `$$target` | unknown entry - inert today, refused anyway |
#: | `$` + newline / continuation / whitespace in `${ }` | unknown entry - refused anyway |
#: | `$x` inside a quoted string | left as text - harmless, but refused (see below) |
#: | `$x` in a `#codeStream` header | **compiled a shared library** |
#:
#: So the only safe answer is: a bare word, optionally braced, optionally with `/` sub-entry
#: segments that are themselves bare words. Everything else - `!`, `:`, `.`, a dot-segment, an
#: absolute path, a space, a `$`, a `"` - is refused.
#:
#: **A `$` inside a quoted string is refused with the rest**, even though OF14 leaves it as text.
#: Distinguishing quoting would mean modelling the reader's string rules, and a `$` in a quoted
#: value is not something Stokes' own writer emits - so the simpler rule costs nothing real and
#: cannot be walked around by a quoting subtlety I have not thought of.
#: **The match must consume the WHOLE macro, so it is anchored on what may FOLLOW it.**
#:
#: A first version matched `$constant/geometry/part` out of
#: `$constant/geometry/part.stl!secretEntry` and stopped at the `.`, so the `!secretEntry`
#: continuation was never examined and **the proven attack was ALLOWED**. A macro can be continued
#: by any of `. / ! : $ { }` or a word character - all of which appear in the forms the matrix
#: probed - so the character after the match must be none of them, or the match fails and the form
#: is refused.
_MACRO_ALLOWED = re.compile(
    r"\$\{?"
    # **The legitimate shapes, every one probed on OF14 and used by its own tutorials:**
    #
    #   `$target`                       an entry in this dictionary
    #   `$sub/inner`                    a sub-entry - `value 5` in the probe
    #   `$!sub/inner`                   rooted at THIS dictionary - `$!backgroundMesh/xCells`
    #   `$../../owner/patchFields`      the INHERITANCE idiom (`TJunctionFan`)
    #   `$dict!sub/leaf`                **another DICTIONARY in the case**:
    #                                   `$fvSchemes!ddtSchemes/default` appears uncommented in
    #                                   `cylinder/system/fvSolution`, and `system/fvSchemes` exists
    #                                   beside it. Probed: OF14 says `Included dictionary file
    #                                   "system/fvSchemes"`.
    # **`..` and `!` may not appear together** - probed: `$../system/dict!inner` returned `value 5`
    # from `system/dict`, so a parent segment before the `!` IS a file read. Each is legitimate
    # alone (`$../../owner/patchFields` inherits; `$fvSchemes!ddtSchemes/default` names a dictionary
    # in the case) and the pair is the shape that reaches an arbitrary file.
    r"(?:(?:\.\./)*[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*"
    r"|!?[A-Za-z_][A-Za-z0-9_]*(?:[!/][A-Za-z_][A-Za-z0-9_]*)*)"
    # **Fails closed: what may FOLLOW the macro is a white-list, not a black-list.**
    #
    # A black-list of continuation characters is bounded by imagination - `$foo->bar` was accepted
    # because `>` was not on my list, and the next spelling would find another omission. Requiring
    # the macro to end at a legitimate value boundary (whitespace, a statement end, a bracket, a
    # quote, a comment) inverts it: an unlisted neighbour refuses the form.
    r"\}?(?=[\s;)}\"'#]|$)"
)

#: The refusal, naming the consequence the probe demonstrated.
_FILE_MACRO_WHY = (
    "a $ macro must be a plain entry reference: OpenFOAM reads other `$` forms as sub-entries, "
    "paths or files - `$file!entry` was probed and includes the file as a dictionary"
)


#: A `$` inside a double-quoted string is LITERAL TEXT, not a macro.
#:
#: Probed on OF14: `commsDir "${FOAM_CASE}/comms";` comes back verbatim, unexpanded. So a quoted
#: value cannot reach a file through `$`, and refusing it would be a false refusal - which the
#: corpus showed with three of OF14's own files.
_QUOTED = re.compile(r'"(?:\\.|[^"\\])*"')

#: The NESTED environment-variable include, `${${VAR}/path/entry}`.
#:
#: **Legitimate, and OF14's own tutorials use it**: `fluid/stackPlume/system/fvConstraints` has
#: `min ${${FOAM_CASE}/0/T!TWind};`, and the probe shows OpenFOAM including
#: `"$FOAM_CASE/0/T"` - a field file inside the case. `fluid/concentricCylinders/0/T.orig` does the
#: same with `constant/physicalProperties`.
#:
#: **Allowed only with a leading `${${VAR}` and no `geometry` or `.stl` anywhere**, because that is
#: the whole difference between this idiom and the attack: the attack reaches the staged SURFACES,
#: which are data for snappy and never a dictionary. A pattern this specific is safe to allow
#: because it cannot express a general path.
_NESTED_INCLUDE = re.compile(
    r"\$\{\$\{[A-Za-z_][A-Za-z0-9_]*\}"
    r"(?P<rest>[^\s;{}\"']*)"
    r"\}"
)


def _unsafe_nested(text: str) -> str | None:
    """Why a nested `${${VAR}/path!entry}` include may not stand, or None when it may.

    Refused when the path reaches the STAGED SURFACES - `geometry` or a `.stl` - which is the one
    place this idiom and the proven attack differ.
    """
    for match in _NESTED_INCLUDE.finditer(text):
        rest = match.group("rest").replace("\\", "/").lower()
        if "geometry" in rest or ".stl" in rest:
            return _FILE_MACRO_WHY
    return None


def _unsafe_macro(text: str) -> str | None:
    """The first `$` form in ``text`` that is not a permitted reference, or None.

    **Every `$` is judged, not the dangerous-looking ones.** Scanning for `$` and allowing only the
    permitted shape is what makes a new spelling safe by default: an unknown form fails the match
    rather than passing a test nobody wrote.

    **Quoted strings are skipped**, because a `$` inside one is text (probed). They are blanked
    rather than removed so an offset still points into the file.
    """
    # Quoted strings are skipped, because a `$` inside one is literal text (probed). They are
    # blanked rather than removed so an offset still points into the file.
    masked = _QUOTED.sub(lambda m: " " * len(m.group(0)), text)
    # **The nested include is blanked too, once it is known to be legitimate.** It is the one form
    # that genuinely reads a file and IS an OF14 idiom, so it is judged by its own rule first and
    # then masked out of the allow-list's way - a shape this specific cannot express a general path.
    nested_why = _unsafe_nested(masked)
    if nested_why is not None:
        return nested_why
    masked = _NESTED_INCLUDE.sub(lambda m: " " * len(m.group(0)), masked)
    index = 0
    while True:
        at = masked.find("$", index)
        if at < 0:
            return None
        match = _MACRO_ALLOWED.match(masked, at)
        if match is None:
            return _FILE_MACRO_WHY
        index = match.end()


def _fold_include(candidate: str) -> str:
    """A path folded the way a filesystem would before the rule reads it.

    Lowercased, backslashes normalised, each component stripped of trailing dots and spaces (which
    Windows drops), and any alternate-data-stream suffix (`file:stream`) cut off. **Every one of
    those was a way past the rule as written**: a check on the raw text is a check on a string the
    filesystem does not agree with.
    """
    text = candidate.replace("\\", "/").lower()
    parts: list[str] = []
    for part in text.split("/"):
        # `x.stl:stream` names the same file as `x.stl` for a directory listing and a different
        # stream for a reader: judging the whole component would miss it either way.
        stem = part.split(":", 1)[0]
        parts.append(stem.rstrip(". "))
    return "/".join(parts)


def _current_include_etc(target: str) -> str | None:
    """The current profile's `#includeEtc` rule: one of the exact names, nothing else."""
    if target in ALLOWED_INCLUDE_ETC:
        return None
    return "only these etc files are allowed: " + ", ".join(ALLOWED_INCLUDE_ETC)


def _current_include_func(name: str) -> str | None:
    """The current profile's `#includeFunc` rule: a function Stokes writes."""
    if name in ALLOWED_INCLUDE_FUNC:
        return None
    return "the function is not one Stokes writes; allowed: " + ", ".join(
        sorted(ALLOWED_INCLUDE_FUNC)
    )


def _include_findings(
    path: str, clean: str, out: list[tuple[int, Finding]], rules: LintRules
) -> None:
    """The `#include`, `#includeEtc` and `#includeFunc` rules, wherever they appear."""
    for match in _INCLUDE_RE.finditer(clean):
        kind = (match.group("kind") or "").lower()
        spelled = match.group()
        if kind == "ifpresent":
            continue  # refused outright by REFUSED_DIRECTIVES
        at = match.start()
        if kind == "func":
            func = _func_finding(path, clean, match, rules.include_func)
            if func is not None:
                out.append((at, func))
            continue
        argument = _QUOTED_ARG_RE.match(clean, match.end())
        if argument is None:
            out.append(
                (
                    at,
                    Finding(path, 0, spelled, "the argument is not a quoted path"),
                )
            )
            continue
        target = argument.group("target")
        if kind == "etc":
            etc_why = rules.include_etc(target)
            if etc_why is not None:
                out.append((at, Finding(path, 0, f"{spelled} {target}", etc_why)))
            continue
        why = rules.include(target, path)
        if why is not None:
            out.append((at, Finding(path, 0, f'{spelled} "{target}"', why)))


def _func_finding(
    path: str, clean: str, match: re.Match[str], name_rule: Verdict
) -> Finding | None:
    """The `#includeFunc` rule: a known function name and arguments that name no path."""
    named = _FUNC_NAME_RE.match(clean, match.end())
    name = named.group("name") if named else ""
    spelled = match.group()
    name_why = name_rule(name)
    if name_why is not None:
        return Finding(path, 0, f"{spelled} {name}".strip(), name_why)
    start = named.end() if named else match.end()
    # **Whitespace before the `(` is allowed by the reader and was the bypass.** This tested
    # `clean[start] == "("`, so `#includeFunc patchAverage (x=/etc/passwd)` left `start` on
    # the space,
    # the check was skipped entirely, and the arguments went unread - while
    # `patchAverage(x=/etc/passwd)` was refused. The same call, one space apart.
    while start < len(clean) and clean[start] in " \t\r\n":
        start += 1
    if clean[start : start + 1] == "(":
        depth = 0
        end = start
        for end in range(start, min(len(clean), start + MAX_LINE_CHARS)):
            char = clean[end]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
        arguments = clean[start : end + 1]
        # **An unclosed argument list is refused.** The scan runs to its cap when no `)`
        # closes it, so
        # `arguments` becomes everything after the call - which means the "no path in the arguments"
        # test below is judging text that is NOT arguments, and a path further down the file
        # is read as
        # one. Refusing is both correct (OpenFOAM cannot read it either) and honest about what
        # the check
        # can vouch for.
        if depth != 0:
            return Finding(
                path,
                0,
                f"{spelled} {name}"[:120],
                "the argument list of this function is never closed, so what follows it cannot be "
                "checked as an argument list",
            )
        if any(bad in arguments for bad in ("..", "$", "~", "/", "\\")):
            return Finding(
                path,
                0,
                f"{spelled} {name}{arguments}"[:120],
                "the arguments name a path or expand a variable",
            )
    return None


def _include_model_finding(path: str, clean: str, match: re.Match[str]) -> Finding | None:
    """The `#includeModel` rule: a **bare model name**, and nothing else.

    It resolves a name against the installation's `caseDicts` exactly as `#includeFunc` does, so it
    gets the same two checks - and it had neither, because `_INCLUDE_RE`'s lookahead
    (`(?![A-Za-z0-9_])`) never matched it, so the directive sat on the allow-list unread (Opus
    item 5).

    **The form is a bare word.** All four uses in the installation are `#includeModel gradP`, and
    `includeModelEntry.C` reads one token from the stream. A quoted argument, a parenthesis, a
    path or
    an absolute anything is not this directive, so anything but one plain name is refused.
    """
    rest = clean[match.end() :]
    # **`name` or `name(args)`, then the end of the statement.** Both forms are real, and both were
    # found by running the corpus rather than guessed: `#includeModel gradP` (no semicolon) in
    # `moodyChart`, and `#includeModel dynamicParcelCloud(name=cloud)` in `stackPlume`. My first
    # version required a bare word, refused the second, and the corpus said so - which is the whole
    # reason that check exists.
    call = re.match(r"[ \t]*(?P<name>[A-Za-z0-9_]+)[ \t]*(?P<args>\([^)]*\))?[ \t]*;?", rest)
    if call is None:
        return Finding(
            path,
            0,
            f"{match.group()} {rest.splitlines()[0][:40] if rest else ''}".strip()[:120],
            f"`{match.group()}` takes a name and an optional argument list; this is not that",
        )
    # **The name is a name, not a path**, and the arguments may not carry one. `#includeFunc`'s rule
    # checks the same things, for the same reason: the directive resolves against the installation.
    arguments = call.group("args") or ""
    if any(bad in arguments for bad in ("..", "$", "~", "/", "\\")):
        return Finding(
            path,
            0,
            f"{match.group()} {call.group('name')}{arguments}"[:120],
            "the arguments name a path or expand a variable",
        )
    # **What follows is the rest of the DICTIONARY, not a fault in this directive.**
    # `#includeModel clouds(name=fvModel)` sits inside a `clouds { ... }` block, so the next
    # character is that block's `}`. My first version refused anything but a semicolon or a newline
    # after the call and refused three real files - found by the corpus, not by reasoning. The two
    # checks above are what matter: a NAME that is not a path, and arguments that name none.
    return None


def _statement_findings(path: str, clean: str, out: list[tuple[int, Finding]]) -> None:
    """The rules that need to know which word is the keyword and which are its values.

    A statement is the run of words and strings between `;`, `{` and `}`; its first token is the
    keyword. **Any value that contains `coded`** is refused (so `t codedMixed;` and
    `type "codedMixed";` both are), and a `$` in the value of a keyword that loads or runs code
    is refused (so `type $t;` is, whatever `t` holds).
    """
    # **A pre-pass over the whole file, before the tokenizer splits it.**
    #
    # `{` and `}` are statement TERMINATORS here (`close()` is called on each), so a `${...}` macro
    # is never one statement's text: measured, `x ${geometry/part.stl!a};` tokenises as
    # `x`, `$`, `{`, `geometry/part.stl!a`, `}`, `;`, and a statement-level check therefore never
    # saw the macro whole. Scanning the cleaned text sees the form OpenFOAM actually reads, and it
    # costs one pass over a file already in memory.
    #
    # **The rule is an allow-list** (Opus's R3): every `$` must match `_MACRO_ALLOWED` - a bare
    # word, optionally braced, optionally with `/` sub-entry segments. Anything else is refused,
    # so a spelling nobody has tried fails by default rather than passing a test nobody wrote.
    macro_why = _unsafe_macro(clean)
    if macro_why is not None:
        out.append((clean.find("$"), Finding(path, 0, "", macro_why)))

    statement: list[tuple[int, str]] = []

    def close() -> None:
        if not statement:
            return
        offset, head = statement[0]
        head_text = head[1:-1] if head.startswith('"') else head
        # **A quoted key with regex metacharacters is a PATTERN to OpenFOAM**, so it can name
        # an entry
        # the word rules cannot see: `"l[i]bs" ("evil.so");` selects `libs` and loads a shared
        # library.
        # Refused unless it is one of the exact patterns our writer emits.
        # **Does this key, read as the regex OpenFOAM reads it as, MATCH a refused word?** That is
        # the exact property: `"l[i]bs"` matches `libs` and hides it; `"div\\(alphaRhoPhi,(h|e)\\)"`
        # matches no refused word and is a legitimate scheme pattern the install ships.
        #
        # **Not "contains a metacharacter".** That version refused 722 real keys across the
        # tutorials -
        # every `fvSchemes` field pattern - which the corpus check caught and no unit test
        # would have.
        # Testing what the pattern MATCHES is what makes the rule tight in both directions.
        if (
            head.startswith('"')
            and head_text not in ALLOWED_PATTERN_KEYS
            and any(ch in head_text for ch in PATTERN_META)
        ):
            # **A pattern-matching key only HIDES something if its statement can act on it.** The
            # bypass is `"l[i]bs" ("evil.so");` - a pattern AND a quoted library path. A field
            # pattern
            # is `"f.*" 1;` - a pattern and a NUMBER. Matching alone refused `"f.*"`,
            # `"(Yi|f|kappa).*"`
            # and 170 keys like them across the tutorials, because `f.*` matches
            # `functionObjectLibs` as a regex even though it sits in `fvSchemes`, where no
            # such entry
            # exists: a regex key is matched against entries **in its own dictionary**, and
            # this lint
            # has no parser to know the siblings. Requiring a quoted value is the closest testable
            # property: the bypass always carries one, a field pattern never does.
            hidden = _pattern_hides_a_refused_word(head_text)
            # The statement's values, before `values` is built below: any token that starts with a
            # quote is a string the key could act on.
            carries_a_string = any(text.startswith('"') for _, text in statement[1:])
            # **A pattern key is refused when the entry it opens ACTS.** My adversarial pass found
            # the shape this exists for: `"syste[m]call"` with `execute "touch ..."` on FOLLOWING
            # lines is a separate statement, so the test above never saw it - and `systemCall` is an
            # entry the solver runs.
            #
            # **Not "opens a block".** That was my first attempt and the corpus refused 8 real
            # `fvSolution` keys for it: `"f.*" { solver PBiCGStab; ... }` is a field selection that
            # legitimately opens a sub-dictionary. What separates them is what is INSIDE - a solver
            # sub-dictionary has `solver`, `tolerance`, `relTol`; the threat has `execute` or one of
            # the coded words. So the test is the block's own content.
            block = _block_text(clean, statement[-1][0] + len(statement[-1][1])).lower()
            acts = carries_a_string or any(
                word in block for word in (*ACTION_WORDS, *REFUSED_WORDS)
            )
            # **A value that is NOT a number is refused too** (Opus item 6). The flags above missed
            # the unquoted forms, and OpenFOAM reads all of them - measured with `foamDictionary`:
            # `libs (libA.so libB.so);` and `libs libA.so;` are as valid as the quoted list, so
            # `"l[i]bs" (a.so);` hid a library while the rule sat looking for a quoted string.
            #
            # **Numeric is the test because a number cannot act.** `"f.*" 1;` and the `fvSchemes`
            # patterns carry one; a solver sub-dictionary's keys and tolerances are numbers; a
            # library
            # name, a path and a word are not. This is the report's own rule, and it is tighter than
            # "carries a string" in one direction and looser in none that matters.
            non_numeric = any(not _is_numeric(text) for _, text in statement[1:] if text != "{")
            if hidden is not None and (acts or non_numeric):
                out.append(
                    (
                        offset,
                        Finding(
                            path,
                            0,
                            head[:120],
                            f"a quoted key is matched as a pattern and it matches the refused word "
                            f"{hidden!r}, so that entry is invisible to this check",
                        ),
                    )
                )
                # **`close()` must clear the statement on EVERY exit** (Opus item 6). Without this
                # the tokens of the refused statement stayed in the list, so the next statement's
                # tokens merged into it - and one refused key was reported once per closing brace
                # that followed. Measured: four findings for one key. The file is refused either
                # way, so this is completeness and message quality, not a missed catch - but a
                # report that repeats itself four times is a report nobody reads.
                statement.clear()
                return
        keyword = re.split(r"[($]", head_text, maxsplit=1)[0]
        values = [text for _, text in statement[1:]]
        if len(keyword) < len(head_text):
            values.insert(0, head_text[len(keyword) :])
        # **`coded` is refused as a TOKEN in a value, not as a substring of one** (FIXES-C item 6).
        # The substring test refused `patch encoded_wall;` and `patch decodedOutlet;` -
        # ordinary names,
        # and every such name a user picks is refused for a C++ hook it does not contain. OpenFOAM's
        # hooks are `coded`, `codedFixedValue`, `codedMixed`, ... : the word `coded` at the
        # START of a
        # value token, or the whole token. `encoded_wall` has it in the middle and is not a hook.
        for value in values:
            plain = value.strip('"')
            head_word = re.split(r"[^A-Za-z0-9_]", plain, maxsplit=1)[0].lower()
            if head_word == "coded" or head_word.startswith("coded"):
                out.append(
                    (
                        offset,
                        Finding(path, 0, f"{keyword} {plain}"[:120], "compiles C++ while it runs"),
                    )
                )
                break
        # **A path-writing key, and any value that looks like an absolute path, a `..` or a `~`.**
        # `timeActivatedFileUpdate { fileToUpdate "/etc/passwd"; }` writes a file of the
        # dictionary's
        # choosing as the agent user and needs no library and no C++; it is the second half of
        # item 3's
        # chain. The `..` and `~` refusal is deliberately broader than the include rule, which only
        # checked paths inside an `#include`: a value is a value wherever it appears.
        key = keyword.lower()
        if key in PATH_WRITING_KEYS:
            out.append(
                (
                    offset,
                    Finding(
                        path,
                        0,
                        head[:120],
                        "this keyword reads or writes a path of the dictionary's choosing",
                    ),
                )
            )
            return
        # **A value-level absolute-path check lived here and was UNREACHABLE** (Opus item 6). It was
        # guarded by `key in PATH_WRITING_KEYS`, and the branch above returns for exactly that set -
        # so it could never run, and the path-writing key is refused whole anyway, which is the
        # stronger answer. Deleted rather than left as a rule a reader would believe was working.
        # The reasoning it carried is kept because the scoping decision still matters: a first
        # version checked EVERY value in EVERY statement and refused `name "/bin/sh"` inside a job's
        # own `state.json`, the agent's legitimate record of the process it started.
        if keyword.lower() in DOLLAR_REFUSED_KEYWORDS and any("$" in value for value in values):
            out.append(
                (
                    offset,
                    Finding(
                        path,
                        0,
                        f"{keyword} $…",
                        "a value that is expanded from a variable cannot be checked",
                    ),
                )
            )
        statement.clear()

    for match in _TOKEN_RE.finditer(clean):
        if match.lastgroup == "punct":
            close()
        else:
            statement.append((match.start(), match.group()))
    close()


def _line_findings(path: str, text: str) -> list[tuple[int, Finding]]:
    """Over-long lines, which a scan is not built for and OpenFOAM does not write."""
    found: list[tuple[int, Finding]] = []
    offset = 0
    for number, line in enumerate(text.split("\n"), start=1):
        if len(line) > MAX_LINE_CHARS:
            found.append(
                (offset, Finding(path, number, "", f"the line is over {MAX_LINE_CHARS} characters"))
            )
        offset += len(line) + 1
    return found


#: The current profile: ADR-0024 §1.8 as it stood before ADR-0029, with ONE change - stricter, not
#: looser: reserved include components are now judged folded (`_reserved_include`), so a trailing
#: dot or space, case, or a `:stream` suffix no longer slips past `geometry`/`polyMesh`.
#: `#include "geometry./x"` was accepted at the previous HEAD and is refused now (Windows fix).
CURRENT_RULES = LintRules(
    directives=ALLOWED_DIRECTIVES,
    include_etc=_current_include_etc,
    include_func=_current_include_func,
    include=_is_unsafe_include,
    words=REFUSED_WORDS,
)


def lint_text(path: str, text: str, *, polymesh: bool = False) -> list[Finding]:
    """Every reason ``text`` may not be staged as ``path``.

    ``polymesh`` (or a ``path`` under `constant/polyMesh/`) relaxes the rules to the one check that
    folder gets: **no** `#` directive at all, because those files are machine-written by the
    mesher and have no legitimate reason to include anything; and nothing may `#include` from
    there either (`_is_unsafe_include`).

    **Always the current profile (ADR-0024 §1.8).** There is deliberately no argument that selects
    another one; the `external` profile is reached only through `lint_external`.
    """
    return lint_with_rules(path, text, polymesh=polymesh, rules=CURRENT_RULES)


def lint_with_rules(
    path: str, text: str, *, polymesh: bool = False, rules: LintRules
) -> list[Finding]:
    """`lint_text` under an explicit profile. Callers outside this package use `lint_text`."""
    if len(text.encode("utf-8", errors="replace")) > MAX_SCAN_BYTES:
        return [
            Finding(
                path,
                0,
                "",
                f"the file is larger than the {MAX_SCAN_BYTES}-byte scan limit, so it was not read",
            )
        ]
    clean = strip_comments(text)
    lowered = clean.lower()
    hits = _line_findings(path, text)
    if polymesh or path.startswith(POLYMESH_PREFIX):
        for match in _HASH_RE.finditer(clean):
            hits.append(
                (
                    match.start(),
                    Finding(path, 0, match.group(), "a mesher-written file has no directives"),
                )
            )
            if len(hits) >= MAX_FINDINGS:
                break
        return _numbered(clean, hits)
    if "#" in clean:
        # **A verbatim string with no closing `#}` is refused outright**, whatever it holds.
        # Everything after the `#{` is a string to OpenFOAM and invisible to any reader
        # that does not know the form, so a file carrying one is not one this lint can
        # vouch for, and "the contents looked harmless" is not a reason to stage it. This
        # is separate from the scanning rule: scanning catches the dangerous words, this
        # catches the malformed file.
        for found in re.finditer(r"#\{", clean):
            tail = clean[found.start() :]
            if "#}" not in tail:
                hits.append(
                    (
                        found.start(),
                        Finding(
                            path,
                            0,
                            "#{",
                            "a verbatim string is never closed, so the rest of the file is "
                            "data to OpenFOAM and unreadable to this check",
                        ),
                    )
                )
        # **Every `#` word must be one we allow.** `_HASH_RE` finds a directive wherever it appears,
        # so an unknown one - `#includeFvModel`, a typo, a directive from a future OpenFOAM - is
        # refused rather than ignored. The named dangerous ones keep their own reason; the
        # rest get a
        # generic one that says the word is not allowed here.
        for found in _HASH_RE.finditer(clean):
            word = found.group()
            name = word[1:].lower()
            # **`#{` and `#}` are a verbatim string's delimiters, not directives.** A bare `#`
            # followed by a brace is the only case `_HASH_RE` matches that is not a directive
            # at all,
            # and refusing it broke every real `#{ ... #}` expression - caught by the false-refusal
            # test, which is what that test is for.
            if name in ("", "{", "}"):
                continue
            if name in rules.directives:
                continue
            why = REFUSED_DIRECTIVE_WHY.get(
                name,
                "not a directive this case writer emits, so it may not be staged "
                f"(allowed: {', '.join(sorted('#' + d for d in rules.directives))})",
            )
            hits.append((found.start(), Finding(path, 0, word, why)))
        _include_findings(path, clean, hits, rules)
        # **`#includeModel` is walked separately**, because `_INCLUDE_RE` cannot match it (see the
        # regex's comment). A directive on the allow-list with no rule behind it is the hole item 5
        # names, so the allow-listing and the checking live next to each other.
        for found in _INCLUDE_MODEL_RE.finditer(clean):
            model = _include_model_finding(path, clean, found)
            if model is not None:
                hits.append((found.start(), model))
    for found in rules.words_re.finditer(lowered):
        hits.append(
            (
                found.start(),
                Finding(path, 0, clean[found.start() : found.end()], rules.words[found.group()]),
            )
        )
        if len(hits) >= MAX_FINDINGS * 2:
            break
    rules.further(path, clean, hits)
    # **The gate used to be `"$" in clean or "coded" in lowered`**, which skipped the
    # statement rules
    # for a dictionary with neither - so the regex-key rule added for FIXES-C item 3 never ran for
    # `"l[i]bs" ("evil.so");`. The condition now also opens on a quote, because the statement
    # rules are
    # the only ones that can see a quoted KEY, and on any path-writing keyword name.
    if (
        "$" in clean
        or "coded" in lowered
        or '"' in clean
        or any(key in lowered for key in PATH_WRITING_KEYS)
    ):
        _statement_findings(path, clean, hits)
    return _numbered(clean, hits)


def _numbered(clean: str, hits: list[tuple[int, Finding]]) -> list[Finding]:
    """The findings in file order, each with its 1-based line (over-long lines already have one)."""
    ordered = sorted(hits, key=lambda item: item[0])
    result: list[Finding] = []
    for offset, item in ordered[:MAX_FINDINGS]:
        line = item.line or clean.count("\n", 0, offset) + 1
        result.append(Finding(item.file, line, item.token, item.why))
    return result


def is_binary(raw: bytes) -> bool:
    """Whether bytes are not a text file: a NUL, or not valid UTF-8."""
    if b"\0" in raw:
        return True
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def lint_file(rel: str, path: Path) -> list[Finding]:
    """Lint one file on disk, text or not.

    A symbolic link, a file that cannot be read, a file over `MAX_SCAN_BYTES` and a **binary**
    file are each refused outright: the only binary the agent accepts in a job is what it wrote
    itself (`AGENT_OWN_PREFIXES`), and those are never passed here.

    **Except a staged `.stl`, which is SAMPLED rather than read whole** (see
    :func:`lint_large_stl`). The exception is exactly one path and one suffix, because that is the
    path a CAD surface takes: an ASCII STL is ~260 bytes per triangle, so a 2 M-triangle part is
    half a gigabyte and reading it whole to check that it is text would cost the agent that much
    memory per staged file.
    """
    if path.is_symlink():
        return [Finding(rel, 0, "", "a symbolic link in a job folder")]
    large_stl = is_staged_surface(rel)
    if large_stl:
        return lint_large_stl(rel, path)
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SCAN_BYTES + 1)
    except OSError as exc:
        return [Finding(rel, 0, "", f"the file could not be read: {exc.strerror or exc}")]
    if len(raw) > MAX_SCAN_BYTES:
        return [
            Finding(
                rel,
                0,
                "",
                f"the file is larger than the {MAX_SCAN_BYTES}-byte scan limit, so it was not read",
            )
        ]
    if is_binary(raw):
        return [Finding(rel, 0, "", "a binary file; only text may be staged")]
    return lint_text(rel, raw.decode("utf-8"), polymesh=rel.startswith(POLYMESH_PREFIX))


def is_staged_surface(rel: str) -> bool:
    """Whether ``rel`` is a surface file this module samples instead of reading whole.

    **Exactly `constant/geometry/<name>.stl` - one folder, no subfolders, one suffix.**

    A plain `startswith(GEOMETRY_PREFIX)` was the first version and it was wrong: it accepted
    `constant/geometry/nested/big.stl`, so the exception reached a path the staging rules do not
    otherwise allow and a large file could be hidden one level down (measured - my own test caught
    it, `nested/big.stl` was accepted over the scan limit). The path is split and counted instead,
    so a subfolder cannot be reached by the sampled path at all.
    """
    parts = rel.split("/")
    return (
        len(parts) == 3
        and f"{parts[0]}/{parts[1]}/" == GEOMETRY_PREFIX
        and parts[2].endswith(STL_SUFFIX)
    )


def lint_large_stl(rel: str, path: Path) -> list[Finding]:
    """Check a staged STL **by streaming every byte**, at constant memory.

    ## Why the whole file, and not a sample

    The first version sampled four 256 KiB windows. Opus's review found what that misses: **a
    directive buried between the windows was never read**, so a hostile file passed. I confirmed it
    - a 12 MiB STL with `#codeStream` in the middle linted clean. A sample is a bet that the
    interesting bytes are in it, and for a file the agent is about to hand OpenFOAM, that bet is
    not good enough. This reads every byte and holds one block at a time.

    ## The grammar is checked WHOLE-LINE, not by prefix

    A prefix test accepted `vertex 0 0 0  #codeStream` - a valid keyword, then a directive on the
    same line. Every line must match its pattern completely.

    ## What is still OpenFOAM's job

    That the geometry is *valid*: triangles can be nonsense inside correct syntax, and
    `surfaceFeatures`/`snappyHexMesh` reject that with their own messages. This function's subject
    is the narrower and security-relevant half - the file is a text STL of an acceptable size
    containing nothing but STL.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [Finding(rel, 0, "", f"the file could not be read: {exc.strerror or exc}")]
    if size > MAX_STL_BYTES:
        return [
            Finding(
                rel,
                0,
                "",
                f"the STL is {size} bytes, over the {MAX_STL_BYTES}-byte staging limit",
            )
        ]
    try:
        with path.open("rb") as handle:
            return _scan_stl_blocks(rel, handle)
    except OSError as exc:
        return [Finding(rel, 0, "", f"the file could not be read: {exc.strerror or exc}")]


def _scan_stl_blocks(rel: str, handle: BinaryIO) -> list[Finding]:
    """Stream a staged STL: **a byte-level gate over every byte, then the grammar if warranted**.

    ## Why two stages

    A full per-line grammar check is what Opus asked for, and it is correct - but it costs a Python
    iteration per line, and an STL is ~25 lines per triangle. Measured on thinkyboy: 50 MiB (3 M
    lines) in **2.7 s**, so 500 MiB extrapolates to **~27 s**, against his ~15 s budget. The
    grammar is not the cost; the loop is.

    So the order is inverted. **The security property is checked first, over every byte, at C
    speed**: a staged surface may contain no `#` (a directive of any kind) and no NUL or non-ASCII
    byte (which is what a binary STL has). `bytes.count` over the blob does that in **0.03 s for
    50 MiB - about 0.3 s for 500 MiB**, four orders of magnitude inside the budget. Only if such a
    byte exists does the per-line grammar run, to say exactly which line is wrong.

    That is strictly STRONGER than the sampler it replaces (every byte is examined, not four
    windows) and strong enough for the threat: **a directive cannot exist without a `#`**, and the
    only other way to smuggle one is a byte that is not text.

    ## Why the carry exists

    A block boundary falls wherever a megabyte falls, which is almost never a line boundary, so the
    last partial line of each block is joined to the next: a line split across two reads is ONE
    line. Checking the halves separately is what refused OF14's own valid `terrain.stl` for the tail
    of a `vertex`.
    """
    findings: list[Finding] = []
    carry = b""
    line_number = 0
    first = True
    suspicious = False
    while True:
        block = handle.read(STL_BLOCK_BYTES)
        if not block:
            break
        # **The byte gate, over every byte of every block, at C speed.**
        #
        # Three byte tests, each of which a staged surface must fail, all at C speed:
        #   * `#` - the only way to open a directive in an OpenFOAM file;
        #   * NUL - **the** reliable mark of a binary STL;
        #   * any byte above 0x7f, which is the rest of a binary STL and every other non-text file.
        #
        # **All three are needed, and the NUL one is not covered by `isascii()`**: NUL is valid
        # ASCII, so a gate testing only `isascii()` accepted a file containing NUL bytes - measured,
        # it broke the test that had caught them a moment earlier. A binary STL whose header says
        # `solid ` is caught by the same pair.
        # **Five byte tests now, and the last three are R1** (Opus's hardening review): the gate
        # decided whether the LINE grammar ran at all, so a `$`, `{` or `}` in a `solid`/`endsolid`
        # name skipped the check that would have refused it. Measured before this: `solid x$y` with
        # no `#` anywhere was ACCEPTED. An STL has no macros and no braces, so any of them is a
        # reason to look at the lines.
        if not suspicious and (
            b"#" in block
            or b"$" in block
            or b"{" in block
            or b"}" in block
            or b"\x00" in block
            or not block.isascii()
        ):
            suspicious = True
        data = carry + block
        lines = data.split(b"\n")
        carry = lines.pop()  # an incomplete line: it continues in the next block
        if len(carry) > STL_MAX_LINE_BYTES:
            # **No legitimate STL line is this long**, and reading on would grow the carry without
            # bound - the memory bound this function exists to keep.
            #
            # **A BINARY STL normally lands here, and the message says so.** OpenFOAM's own reader
            # comments that "some programs (eg, pro-STAR) have 'solid' as the first word in the
            # binary header" (`STLsurfaceFormatCore.C:37-42`), and a binary file has almost no
            # newlines - so the whole payload arrives as one enormous line. Saying "too long"
            # without saying "likely binary" sends the user looking for a long line that is not
            # there. Measured on a 5 KB pro-STAR-style file: refused here, not by the NUL test,
            # because the read is block-wise and the line cap fires first.
            binary = b"\x00" in carry[:4096] or not carry[:4096].isascii()
            too_long = (
                "the file looks like a BINARY STL (no line breaks, non-text bytes); a staged "
                "surface must be ASCII - the server converts a binary STL before staging"
                if binary
                else "a line is too long to be an STL line"
            )
            findings.append(Finding(rel, line_number + 1, "", too_long))
            return findings
        if not suspicious:
            # **The fast path, and what it still does not check**: the GRAMMAR of a line that is
            # plain text and carries none of the bytes the gate looks for.
            #
            # The gate is what decides whether the per-line pass runs, so its byte set IS the
            # design. It holds the bytes that can matter:
            #
            #   * `#` - a directive, and a directive is what executes;
            #   * `$`, `{`, `}` - **added by Opus's hardening review (R1)**, because they too were a
            #     reason to look: measured, `solid x$y` with no `#` anywhere was ACCEPTED, since the
            #     gate never fired and the line grammar that refuses a `$` never ran;
            #   * NUL and any byte above 0x7f - which is what a binary STL has, and `isascii()`
            #     alone misses NUL because NUL IS valid ASCII.
            #
            # What remains unexamined is text junk carrying none of those (`NOT STL AT ALL`), which
            # OpenFOAM rejects at `surfaceFeatures`/`snappyHexMesh` with a line number of its own. A
            # full per-line pass would cost ~26 s on a 500 MiB part, so it is refused where it is
            # already refused rather than adding half a minute to every staged part.
            line_number += len(lines)
            if lines:
                first = False
            continue
        for raw in lines:
            line_number += 1
            word = raw.strip()
            if not word:
                continue
            why = _stl_line_problem(word, first=first)
            first = False
            if why is not None:
                findings.append(Finding(rel, line_number, "", why))
                if len(findings) >= MAX_STL_FINDINGS:
                    return findings
    if carry.strip():
        line_number += 1
        why = _stl_line_problem(carry.strip(), first=first)
        if why is not None:
            findings.append(Finding(rel, line_number, "", why))
    # **A `#` that no line rule named is left alone**: a `solid` NAME may legally contain one, and
    # it is data to snappy - never parsed as a directive, because `_is_unsafe_include` refuses to
    # let any dictionary include this file. Refusing it would be a false refusal on a valid surface.
    return findings


def _stl_line_problem(raw: bytes, *, first: bool) -> str | None:
    """Why one line of a staged STL is not acceptable, or None when it is.

    ``first`` relaxes the rule for the file's first non-blank line, where a `solid` name may be
    free text: importers put a path, a timestamp or a part number there.
    """
    # **An STL has no macros** (claude-win2's 2310), so a `$` in one is an attempt to have the file
    # read as something else - and `$file!entry` reaching an STL is exactly how a directive inside
    # one executed (probed). Refused on the line rather than left to the number grammar, so the
    # message names the real problem.
    for byte, why in (
        (b"$", "a `$` in a staged surface; an STL has no macros"),
        (b"\x00", "a NUL byte; a staged surface must be a text STL"),
    ):
        if byte in raw:
            return why
    # **The NUL test is the reliable binary signal.** A binary STL is 80 header bytes, a triangle
    # count, then packed floats - and the header MAY begin `solid `, so a check on the first bytes
    # alone was not enough. Packed floats contain NULs within the first triangle.
    try:
        line = raw.decode("ascii")
    except UnicodeDecodeError:
        return "a non-ASCII byte; a staged surface must be a text STL"
    if len(line) > STL_MAX_LINE_BYTES:
        return "a line is too long to be an STL line"
    if _STL_LINE_RE.fullmatch(line.strip()) is not None:
        return None
    if first and _STL_SOLID_RE.fullmatch(line.strip()) is not None:
        return None
    return f"not STL syntax: {line.strip()[:60]!r}"


def lint_job(
    job_dir: Path,
    rel_paths: list[str] | None = None,
    trusted_polymesh: Mapping[str, int] | None = None,
) -> list[Finding]:
    """Lint every file under a job folder, whatever its name or folder.

    **Everything, not `system/`, `constant/` and `0/`.** `100/U`, `extra/evil`, a time directory
    and any other folder are as readable by an `#include` as the three staged ones. What is skipped
    is only what the agent wrote itself: the `artifacts/` folder and the `log.*` files at the top.
    A symbolic link anywhere is a finding. ``rel_paths`` limits the scan to those files.

    ``trusted_polymesh`` maps a ``constant/polyMesh/<name>`` path to the size the agent recorded
    for it when its own mesher wrote it (or when it was copied from a job whose mesher did). A file
    there whose size still matches is not scanned, which is what lets a mesh over the size limit,
    or a binary one, through. **Only that prefix**, only a regular file and only an exact size
    match: any other file, and any trusted one that changed, is linted in full.
    """
    found: list[Finding] = []
    trusted = trusted_polymesh or {}
    for rel, path in _job_files(job_dir, rel_paths, found):
        if _is_trusted_polymesh(rel, path, trusted):
            continue
        found.extend(lint_file(rel, path))
    return found


def _is_trusted_polymesh(rel: str, path: Path, trusted: Mapping[str, int]) -> bool:
    """Whether ``rel`` is a mesher-written polyMesh file whose size is still the recorded one."""
    recorded = trusted.get(rel)
    if not rel.startswith(POLYMESH_PREFIX) or not isinstance(recorded, int):
        return False
    try:
        return not path.is_symlink() and path.is_file() and path.stat().st_size == recorded
    except OSError:
        return False


def _job_files(
    job_dir: Path, rel_paths: list[str] | None, found: list[Finding]
) -> list[tuple[str, Path]]:
    """The files to scan, as ``(relative path, path)``; links met on the way go into ``found``."""
    if rel_paths is not None:
        return [
            (rel, job_dir / rel)
            for rel in rel_paths
            if isinstance(rel, str) and ((job_dir / rel).is_file() or (job_dir / rel).is_symlink())
        ]
    files: list[tuple[str, Path]] = []
    for root, dirnames, filenames in os.walk(job_dir, followlinks=False):
        here = Path(root)
        top = here == job_dir
        dirnames.sort()
        for name in list(dirnames):
            if (here / name).is_symlink():
                rel = (here / name).relative_to(job_dir).as_posix()
                found.append(Finding(rel, 0, "", "a symbolic link in a job folder"))
                dirnames.remove(name)
        for name in sorted(filenames):
            path = here / name
            rel = path.relative_to(job_dir).as_posix()
            if rel.startswith(AGENT_OWN_PREFIXES) and not path.is_symlink():
                continue
            if top and name.startswith(AGENT_OWN_LOG_PREFIX) and not path.is_symlink():
                continue
            files.append((rel, path))
    return files


def _pattern_hides_a_refused_word(pattern: str) -> str | None:
    """The refused word a quoted key matches when read as a regex, or None.

    OpenFOAM compiles a keyword whose `isPattern()` is true into a `regExp` and matches
    lookups against
    it (`dictionary.C`), so `"l[i]bs"` names the `libs` entry while the word rules see `l[i]bs`.

    **The test is what the pattern MATCHES, not what characters it contains.** A key that matches no
    refused word cannot hide one, however many metacharacters it has - which is true of every
    `fvSchemes` field pattern OpenFOAM ships. The match is anchored, because a keyword lookup
    compares
    the whole name.
    """
    try:
        compiled = re.compile(f"^(?:{pattern})$", re.IGNORECASE)
    except re.error:
        # A pattern OpenFOAM would also reject. Not this rule's business: the file will not read.
        return None
    for word in REFUSED_WORDS:
        if compiled.match(word):
            return word
    return None


def _block_text(text: str, start: int) -> str:
    """The text of the `{ ... }` block beginning at or after ``start``, or "" when there is none.

    One brace match, from the first `{` at or after ``start`` to its partner, counting
    nesting. A key
    with no block after it returns "" - which is the right answer for `"f.*" 1;` as much as for a
    stray word, because there is no block whose content could act.
    """
    opening = text.find("{", start)
    if opening < 0:
        return ""
    depth = 0
    for index in range(opening, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[opening : index + 1]
    return text[opening:]


def _is_numeric(token: str) -> bool:
    """Whether a value token is a number, so it cannot name a file or load anything.

    Accepts what OpenFOAM writes for a scalar: an optionally signed decimal with an exponent,
    and the
    `uniform`/dimensioned forms are NOT this - `uniform (0 0 0)` is refused, correctly, because a
    pattern key that hides a refused word should not carry a vector either. A trailing `;` or `)` is
    stripped first, since the scanner keeps them on the token in some shapes.
    """
    text = token.strip().strip(";").strip(")").strip("(").strip()
    if not text:
        return True
    try:
        float(text)
    except ValueError:
        return False
    return True
