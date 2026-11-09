"""The restricted Allrun parser (ADR-0029 decision 6, plan T-306).

## Responsibility

`parse_allrun` reads an Allrun script **as text** and turns the lines it recognises into steps
`{name, utility, argv, parallel, ranks, region, stage, folder, line}`, file actions (`cp -r 0.orig
0`, `mkdir -p`, `rm -rf`) and resource copies (`cp`/`ln -s $FOAM_TUTORIALS/resources/<rel> <rel>`,
an `ln -s` becoming a staged copy). Every other line is listed in `unparsed` with its line number
and the reason; the recipe is then `partial`. `case_recipe` reads a case folder's Allrun, or falls
back to `recipe.default_recipe`.

## No shell, ever

Nothing here runs a process or evaluates a variable. The only `$` forms recognised are literal
spellings with a fixed meaning: `$(getApplication)`, `$application` and `${application}` (the
controlDict's application, read from the case by the caller), `$FOAM_TUTORIALS/resources/` as the
prefix of a resource path, the working-directory guard `cd ${0%/*} || exit 1` and sourcing
`RunFunctions`. Command substitution, backticks, pipes, redirections, lists (`;`, `&&`, `||`),
globs, `eval`, `source`, assignments, conditionals, loops and functions are listed, never run;
the sole exception is a single filename `*` in a shared-resource copy, expanded by the importer.
A line inside an `if`, a loop or a function body is listed too, because whether it would run is
not known. `cd <folder>` moves the following steps into a sub-case.

Import rules: standard library, `stokes.cases.facts` and `stokes.cases.recipe`. Headless.
"""

from __future__ import annotations

import itertools
import posixpath
import re
import shlex
from collections.abc import Sequence
from pathlib import Path

from stokes.cases.facts import MAX_ALLRUN_BYTES, CaseFacts, read_case
from stokes.cases.recipe import (
    Action,
    Recipe,
    Resource,
    Step,
    Unparsed,
    application_of,
    default_recipe,
    stage_of,
)

MAX_LINES = 5000
MAX_STEPS = 200

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_COMPONENT_RE = re.compile(r"^(?:\.\.|\.|[A-Za-z0-9_][A-Za-z0-9_.\-]*)$")
_RESOURCE_GLOB_RE = re.compile(
    r"^cp \x01res\x01/((?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)*"
    r"[A-Za-z0-9_][A-Za-z0-9_.-]*\*[A-Za-z0-9_.-]*) "
    r"([A-Za-z0-9_./-]+/)$"
)
_RANKS_RE = re.compile(r"^[1-9][0-9]{0,2}$")

#: Literal spellings replaced by markers before the line is judged. `\x01` cannot appear in an
#: accepted line (`_judge` refuses control characters), so a marker cannot be forged.
_APP = "\x01app\x01"
_RES = "\x01res\x01/"
_APP_RE = re.compile(r"\$\(getApplication\)|\$\{application\}|\$application(?![A-Za-z0-9_])")
_RES_RE = re.compile(r"\$(?:FOAM_TUTORIALS|\{FOAM_TUTORIALS\})/resources/")

_GUARDS = frozenset(
    {
        "cd ${0%/*} || exit 1",
        'cd "${0%/*}" || exit 1',
        "cd ${0%/*} || exit",
        'isTest "$@" && exit 0',
    }
)
_RUN_FUNCTIONS = frozenset(
    {
        "$WM_PROJECT_DIR/bin/tools/RunFunctions",
        "${WM_PROJECT_DIR:?}/bin/tools/RunFunctions",
        "${WM_PROJECT_DIR}/bin/tools/RunFunctions",
    }
)
_APPLICATION_LINES = frozenset({"application=$(getApplication)", 'application="$(getApplication)"'})

_BLOCKS = {
    "if": "a conditional",
    "case": "a conditional",
    "for": "a loop",
    "while": "a loop",
    "until": "a loop",
    "{": "a function body or a group",
    "(": "a function body or a group",
}
_CLOSERS = frozenset({"fi", "done", "esac", "}", ")"})
_CONDITIONAL = frozenset({"if", "then", "else", "elif", "fi", "case", "esac"})
_LOOP = frozenset({"for", "while", "until", "do", "done"})
_SHELL_WORDS = {
    "eval": "eval runs text as shell code",
    "exec": "exec replaces the shell",
    "source": "sourcing a script runs its code",
    ".": "sourcing a script runs its code",
    "export": "a variable for later commands",
    "set": "a shell option",
    "unset": "a shell variable",
    "exit": "a shell exit",
    "return": "a shell return",
    "function": "a shell function",
}


def _region(args: Sequence[str]) -> str | None:
    for flag, value in itertools.pairwise(args):
        if flag == "-region":
            return value
    return None


def case_recipe(case: Path, parts: Sequence[str] = ()) -> tuple[Recipe, CaseFacts]:
    """The recipe of the case folder ``case`` (Allrun if it has one, else the default)."""
    facts = read_case(case)
    application = application_of(facts, parts)
    allrun = case / "Allrun"
    if allrun.is_file() and not allrun.is_symlink():
        try:
            if allrun.stat().st_size > MAX_ALLRUN_BYTES:
                raise OSError("Allrun is too large")
            text = allrun.read_bytes().decode("utf-8", errors="replace")
        except OSError as exc:
            recipe = Recipe("allrun")
            recipe.unparsed.append(Unparsed(0, "", f"Allrun could not be read: {exc}"))
            return recipe, facts
        return parse_allrun(text, application), facts
    return (
        default_recipe(application, block_mesh=facts.has_block_mesh_dict, has_mesh=facts.has_mesh),
        facts,
    )


# ---------------------------------------------------------------------------------- the parser


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """`(first line number, text)` with backslash continuations joined; at most `MAX_LINES`."""
    out: list[tuple[int, str]] = []
    pending, start = "", 0
    for number, raw in enumerate(text.splitlines()[:MAX_LINES], start=1):
        if not pending:
            start = number
        line = pending + raw.strip()
        if line.endswith("\\"):
            pending = line[:-1] + " "
            continue
        pending = ""
        out.append((start, line))
    if pending:
        out.append((start, pending.strip()))
    return out


def _strip_comment(line: str) -> str:
    """The line without an unquoted `#` comment (a `#` starting a word)."""
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line


def _shell_feature(line: str) -> str | None:  # noqa: PLR0911 - one return per feature
    """The first shell feature in ``line`` (markers already substituted), or None."""
    if any(ord(char) < 32 and char not in "\t\x01" for char in line):
        return "a control character"
    quote = ""
    for index, char in enumerate(line):
        if quote == "'":
            quote = "" if char == "'" else quote
            continue
        if char == "`" or line.startswith("$(", index):
            return "command substitution"
        if char == "$":
            return "a variable expansion"
        if quote == '"':
            quote = "" if char == '"' else quote
            continue
        if char in "'\"":
            quote = char
        elif char == "|":
            return "a pipe or a command list"
        elif char in "<>":
            return "a redirection"
        elif char in ";&":
            return "a command list"
        elif char in "()":
            return "a subshell"
        elif char in "*?[]{}~":
            return "a glob or a brace expansion"
    return "an unterminated quote" if quote else None


def _relative(path: str, folder: str) -> str | None:
    """``path`` joined to ``folder`` and normalised, or None when it is not a plain relative path
    that stays inside the case."""
    stripped = path.rstrip("/")
    if not stripped or path.startswith("/") or "\\" in path or re.match(r"^[A-Za-z]:", path):
        return None
    if not all(_COMPONENT_RE.match(part) for part in stripped.split("/")):
        return None
    joined = posixpath.normpath(posixpath.join(folder, stripped))
    if joined == ".." or joined.startswith("../"):
        return None
    return "" if joined == "." else joined


def _resource(path: str) -> str | None:
    """`resources/<rel>` for a marker-prefixed resource path, or None."""
    rel = path[len(_RES) :].rstrip("/")
    if not rel or not all(
        _COMPONENT_RE.match(part) and part not in {".", ".."} for part in rel.split("/")
    ):
        return None
    return f"resources/{rel}"


class _Parser:
    """One pass over an Allrun's logical lines."""

    def __init__(self, application: str | None) -> None:
        self.application = application
        self.recipe = Recipe("allrun")
        self.folder = ""
        #: The blocks open at this line, innermost last: what a line inside one is inside of.
        self.blocks: list[str] = []

    def refuse(self, number: int, text: str, why: str) -> None:
        self.recipe.unparsed.append(Unparsed(number, text[:200], why))

    def line(self, number: int, raw: str) -> None:
        text = " ".join(_strip_comment(raw).split())
        if not text or text.startswith("#!"):
            return
        words = re.split(r"[\s;&|]+", text)
        opens = [_BLOCKS[word] for word in words if word in _BLOCKS]
        opens += [_BLOCKS["{"]] * text.count("() {")
        closes = sum(word in _CLOSERS for word in words)
        if self.blocks or opens or closes:
            if opens or closes:
                why = self._block_why(words)
            else:
                why = f"inside {self.blocks[-1]}: not run"
            self.blocks += opens
            del self.blocks[max(0, len(self.blocks) - closes) :]
            self.refuse(number, text, why)
            return
        if text in _GUARDS or text in _APPLICATION_LINES:
            return
        if words[0] in {".", "source"} and len(words) == 2 and words[1] in _RUN_FUNCTIONS:
            return
        self.command(number, text)

    @staticmethod
    def _block_why(words: Sequence[str]) -> str:
        if any(word in _LOOP for word in words):
            return "a loop, or a line inside one: not run"
        if any(word in _CONDITIONAL for word in words):
            return "a conditional, or a line inside one: not run"
        return "a function body or a group: not run"

    def command(self, number: int, text: str) -> None:  # noqa: PLR0911 - each refusal stops parsing
        marked = _RES_RE.sub(_RES, _APP_RE.sub(_APP, text))
        if "\x01" in text:
            self.refuse(number, text, "a control character")
            return
        # A single basename wildcard in a resource-to-directory copy is
        # expanded by the importer, never by a shell.
        glob = _RESOURCE_GLOB_RE.fullmatch(marked)
        if glob:
            source, target = glob.groups()
            if any(part in {".", ".."} for part in source.split("/")):
                self.refuse(number, text, "the resource glob is not a plain path")
                return
            where = _relative(target, self.folder)
            if not where:
                self.refuse(number, text, "the target is not a relative path inside the case")
                return
            self.recipe.resources.append(Resource(f"resources/{source}", where + "/", number))
            return
        why = _shell_feature(marked)
        if why is not None:
            self.refuse(number, text, why)
            return
        try:
            argv = shlex.split(marked, posix=True)
        except ValueError:
            self.refuse(number, text, "the line cannot be split into words")
            return
        head = argv[0]
        if head in _SHELL_WORDS:
            self.refuse(number, text, _SHELL_WORDS[head])
            return
        if "=" in head:
            self.refuse(number, text, "a variable assignment")
            return
        handler = {
            "runApplication": self.run,
            "runParallel": self.run,
            "cp": self.copy,
            "ln": self.link,
            "mkdir": self.mkdir,
            "rm": self.remove,
            "cd": self.cd,
        }.get(head)
        why = handler(number, argv) if handler is not None else "not a command the recipe knows"
        if why is not None:
            self.refuse(number, text, why)

    def run(self, number: int, argv: list[str]) -> str | None:  # noqa: PLR0911 - one per refusal
        parallel = argv[0] == "runParallel"
        suffix: str | None = None
        ranks: int | None = None
        index = 1
        while index < len(argv) and argv[index].startswith("-"):
            flag = argv[index]
            if flag in {"-a", "-append", "-o", "-overwrite"}:
                index += 1
            elif flag in {"-s", "-suffix"} and index + 1 < len(argv):
                suffix = argv[index + 1]
                if not _NAME_RE.match(suffix):
                    return "the log suffix must be a plain name"
                index += 2
            elif flag == "-np" and parallel and index + 1 < len(argv):
                if not _RANKS_RE.match(argv[index + 1]):
                    return "-np must be a whole number of ranks"
                ranks = int(argv[index + 1])
                index += 2
            else:
                return f"{flag} is not a {argv[0]} option the recipe knows"
        if index >= len(argv):
            return f"{argv[0]} names no application"
        utility = argv[index]
        if utility == _APP:
            if not self.application:
                return "the case's application is not known from system/controlDict"
            utility = self.application
        elif not _NAME_RE.match(utility):
            return "the application must be a plain name"
        args = list(argv[index + 1 :])
        resource_why = self._dict_resources(number, args)
        if resource_why is not None:
            return resource_why
        if any("\x01" in arg for arg in args):
            return "a variable expansion in an argument"
        if len(self.recipe.steps) >= MAX_STEPS:
            return f"more than {MAX_STEPS} steps"
        self.recipe.steps.append(
            Step(
                name=f"{utility}.{suffix}" if suffix else utility,
                argv=(utility, *args),
                parallel=parallel,
                ranks=ranks,
                region=_region(args),
                stage=stage_of(utility, self.application),
                folder=self.folder,
                line=number,
            )
        )
        return None

    def _dict_resources(self, number: int, args: list[str]) -> str | None:
        """`-dict $FOAM_TUTORIALS/resources/<rel>` becomes a resource copied into `system/`.

        `blockMesh -dict .../resources/blockMesh/pitzDaily` (two dozen tutorials): the dictionary
        is copied into the case's `system/` and ``args`` is rewritten to name that copy, so the
        step reads a file inside the job.
        """
        for position, arg in enumerate(args):
            if arg.startswith(_RES) and position > 0 and args[position - 1] == "-dict":
                found = _resource(arg)
                if found is None:
                    return "the resource is not a plain path under resources/"
                name = f"system/{posixpath.basename(found)}"
                where = posixpath.join(self.folder, name) if self.folder else name
                self.recipe.resources.append(Resource(found, where, number))
                args[position] = name
        return None

    def _operands(self, argv: list[str], flags: frozenset[str]) -> tuple[list[str], str | None]:
        operands = [word for word in argv[1:] if not word.startswith("-")]
        bad = [word for word in argv[1:] if word.startswith("-") and word not in flags]
        return operands, (f"{bad[0]} is not a {argv[0]} option the recipe knows" if bad else None)

    def copy(self, number: int, argv: list[str]) -> str | None:
        operands, why = self._operands(argv, frozenset({"-r", "-R", "-f", "-rf", "-fr", "-p"}))
        if why is not None:
            return why
        return self._place(number, operands, "cp")

    def link(self, number: int, argv: list[str]) -> str | None:
        operands, why = self._operands(argv, frozenset({"-s", "-sf", "-fs", "-f"}))
        if why is not None:
            return why
        if "-s" not in argv and "-sf" not in argv and "-fs" not in argv:
            return "only a symbolic link (ln -s) is recognised"
        return self._place(number, operands, "ln")

    def _place(  # noqa: PLR0911 - one return per refusal
        self, number: int, operands: list[str], verb: str
    ) -> str | None:
        """`cp`/`ln -s` of a resource or a case path: a resource copy or a copy action."""
        if len(operands) != 2:
            return f"{verb} must name exactly one source and one target"
        source, target = operands
        where = _relative(target, self.folder)
        if where is None:
            return "the target is not a relative path inside the case"
        folder_target = target.endswith("/") or target in {".", "./"}
        if source.startswith(_RES):
            found = _resource(source)
            if found is None:
                return "the resource is not a plain path under resources/"
            self.recipe.resources.append(
                Resource(found, where + "/" if folder_target and where else where, number)
            )
            return None
        if "\x01" in source:
            return "a variable expansion"
        # A symbolic link's relative source is read from the link's own folder, a copy's from
        # the working folder.
        base = self.folder
        if verb == "ln":
            base = where if folder_target else posixpath.dirname(where)
        origin = _relative(source, base)
        if not origin:
            return "the source is not a relative path inside the case"
        if folder_target:
            where = posixpath.join(where, posixpath.basename(origin))
        self.recipe.actions.append(Action("copy", where, origin, number))
        return None

    def mkdir(self, number: int, argv: list[str]) -> str | None:
        operands, why = self._operands(argv, frozenset({"-p"}))
        if why is not None or not operands:
            return why or "mkdir names no folder"
        for operand in operands:
            where = _relative(operand, self.folder)
            if not where:
                return "the folder is not a relative path inside the case"
            self.recipe.actions.append(Action("mkdir", where, None, number))
        return None

    def remove(self, number: int, argv: list[str]) -> str | None:
        operands, why = self._operands(argv, frozenset({"-r", "-R", "-f", "-rf", "-fr"}))
        if why is not None or not operands:
            return why or "rm names nothing"
        for operand in operands:
            where = _relative(operand, self.folder)
            if not where:
                return "the path is not a relative path inside the case"
            self.recipe.actions.append(Action("remove", where, None, number))
        return None

    def cd(self, number: int, argv: list[str]) -> str | None:
        if len(argv) != 2:
            return "cd must name one folder"
        where = _relative(argv[1], self.folder)
        if where is None:
            return "the folder is not a relative path inside the case"
        self.folder = where
        return None


def parse_allrun(text: str, application: str | None) -> Recipe:
    """The recipe an Allrun describes; ``application`` is the controlDict's (or None)."""
    parser = _Parser(application)
    if "\x00" in text:
        parser.refuse(0, "", "Allrun holds binary data")
        return parser.recipe
    for number, line in _logical_lines(text):
        parser.line(number, line)
    return parser.recipe
