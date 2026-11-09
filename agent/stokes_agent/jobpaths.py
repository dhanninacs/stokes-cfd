"""The path and identifier rules a job obeys, as pure functions (ADR-0024, T-144a).

Split out of `jobs.py` for two reasons. It is the **security-relevant** half — every rule here
is a way a request from the desktop could otherwise name something it should not — so it
deserves to be readable and testable on its own, with no process, thread or filesystem state.
And `jobs.py` was over a thousand lines with the two concerns interleaved, which is past the
point where a reviewer can hold it.

## The rules, and what each one stops

* `JOB_ID_RE` — a job id becomes a **directory name**. Validated before any path is built, so an
id like ``../../etc`` cannot be a path at all.
* `REL_PATH_RE` — a staged file name. Rejects a leading ``/``, a backslash, and an empty or trailing
  component.
* `..` — rejected as a segment even though the regex already forbids it, because the two checks
failing independently is worth more than the one that is redundant.
* **Containment on the RESOLVED path** — the rule none of the string checks can replace. A symlink
  inside the job folder pointing at ``/etc`` passes every string test and is caught only here.
* `is_safe_relative` — the same rules for a path *read* from a job, used by `copy_from` and `read`.

Nothing here touches the disk except `resolve()`, which is what makes the containment check real.
"""

from __future__ import annotations

import re
from pathlib import Path

#: A step name: a letter, then up to 31 letters, digits or underscores. It becomes part of a log's
#: file name, so it may not contain a separator or a dot.
STEP_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")

#: A job id, which becomes a directory name.
JOB_ID_RE = re.compile(r"^j_[0-9a-f]{12}$")

#: A path relative to a job folder: no leading separator, no backslash, no empty component.
REL_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*(/[A-Za-z0-9_][A-Za-z0-9_.\-]*)*$")


#: The ONE list of folders a client may stage files into. A client-written path must start with one
#: of these and name a file inside it: the case writer produces `system/`, `constant/` and `0/`
#: (`stokes.compile.of14` and `stokes.mesh.drivers`), and `constant/triSurface` and
#: `constant/polyMesh` live under `constant/`. Everything else in a job folder (`log.*`,
#: `artifacts/`, run output) is the agent's own and a client may not write it.
STAGED_DIRS: tuple[str, ...] = ("system", "constant", "0")

#: The folders an **external** case (ADR-0029) may also stage: a time directory (`0.5`, `100`,
#: `1e-05`) or `0.orig`, which tutorials keep and their Allrun copies to `0`. Never an agent name:
#: the agent's own top-level entries (`log.*`, `monitors/`, `artifacts/`, `postProcessing/` output)
#: are not numbers and not `0.orig`.
EXTERNAL_TOP_RE = re.compile(r"^(?:[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|0\.orig)$")

#: A file name a client may never stage, whatever folder it is in: the agent's own bookkeeping.
_FORBIDDEN_NAMES = frozenset({"state.json", "index.json"})
_FORBIDDEN_SUFFIXES = (".pid", ".part", ".tmp")


class JobPathError(ValueError):
    """A path or an id is not usable inside a job.

    A plain `ValueError` here and not a `StokesError`: this module is the bottom of the stack
    and does not know what a command is. `jobs.py` catches it and raises the typed error with
    the API code.
    """


def check_job_id(job: object) -> str:
    """The job id, or `JobPathError`. **The first check on every entry point.**"""
    if not isinstance(job, str) or not JOB_ID_RE.match(job):
        raise JobPathError(f"{job!r} is not a job id")
    return job


def check_relative(rel: object) -> str:
    """A path relative to a job folder, or `JobPathError`.

    Both checks run: the regex, and an explicit ``..`` segment test. The second is redundant against
    this regex on purpose — a future edit to the pattern would otherwise silently drop the traversal
    rule, and a traversal is not a mistake to discover twice.
    """
    if not isinstance(rel, str) or not rel:
        raise JobPathError(f"{rel!r} is not a relative path inside the job")
    if not REL_PATH_RE.match(rel):
        raise JobPathError(f"{rel!r} is not a relative path inside the job")
    if ".." in rel.split("/"):
        raise JobPathError(f"{rel!r} climbs out of the job")
    return rel


def check_staged_path(rel: object, external: bool = False) -> str:
    """A path a CLIENT may write into a job: `check_relative` plus the staging whitelist.

    **Why the extra rule.** `check_relative` only says a path stays inside the job. That left the
    agent's own files writable by the client: the job record, the artifact index, the logs. Now the
    first component must be one of `STAGED_DIRS`, the path must name a file inside it, no component
    may be a dot-name or `artifacts`, and the agent's own file names and suffixes are refused.
    With ``external`` (a job under the `external` profile) the first component may also be a time
    directory or `0.orig` (`EXTERNAL_TOP_RE`).
    """
    checked = check_relative(rel)
    parts = checked.split("/")
    top_ok = parts[0] in STAGED_DIRS or (external and EXTERNAL_TOP_RE.match(parts[0]) is not None)
    if len(parts) < 2 or not top_ok:
        raise JobPathError(
            f"{checked!r} is not a staged path: a file inside one of {', '.join(STAGED_DIRS)}"
        )
    for part in parts:
        if part.startswith(".") or part == "artifacts":
            raise JobPathError(f"{checked!r} has a component the agent keeps for itself: {part!r}")
    name = parts[-1]
    if name in _FORBIDDEN_NAMES or name.endswith(_FORBIDDEN_SUFFIXES):
        raise JobPathError(f"{checked!r} names a file the agent keeps for itself")
    # **The old `name.startswith("log")` rule is GONE, and it was never doing anything** (FIXES-C
    # item 6, corrected).
    #
    # It refused every one of these:
    #
    #     system/log.solve      system/logarithm      system/logistics      system/log
    #
    # `logarithm` and `logistics` are ordinary names a case may contain, and `log.solve` is what a
    # user would naturally call their own solver log inside `system/`.
    #
    # **Nothing was being protected.** The file the rule meant to guard is the agent's own
    # `log.<step>`, written at the TOP of the job (`jobs.py`: `job_dir / f"log.{step.name}"`) beside
    # `case.foam` and the case folders - and the check above requires the FIRST component to be one
    # of `STAGED_DIRS` = `system`, `constant`, `0`. **A staged path therefore cannot name it**,
    # depth: `system/log.solve` and the root's `log.solve` are different files under `job_dir`.
    #
    # So the rule was a false refusal with no true positive, and the `_FORBIDDEN_NAMES` check above
    # still refuses the agent's real bookkeeping - `state.json`, `index.json` - which is what the
    # name list is for.
    return checked


def job_path(job_dir: Path, rel: object) -> Path:
    """``rel`` under ``job_dir``, validated then **contained on the resolved path**.

    The resolve is the part that matters: a symlink inside the job pointing elsewhere passes every
    string check, and `is_relative_to` on the resolved path is what refuses it.
    """
    checked = check_relative(rel)
    target = job_dir / checked
    if not target.resolve().is_relative_to(job_dir.resolve()):
        raise JobPathError(f"{checked!r} resolves outside the job folder")
    return target


#: The one place a path may carry punctuation a filename normally would not.
#:
#: **Measured, not guessed.** An OpenFOAM function object writes under
#: ``postProcessing/<name>/<time>/<file>`` where ``<name>`` is the function's own invocation —
#: and for a
#: function taking arguments that name is e.g. ``patchAverage(patch=inlet,fields=(pU))``, with
#: parentheses, ``=`` and commas in it. Verified from a real pitzDaily run (T-200).
#: `REL_PATH_RE` refuses
#: it, so reading a result **must** accept these characters in the segment right after
#: ``postProcessing/``.
POSTPROCESSING_PREFIX = "postProcessing/"
_POST_PUNCTUATION = "(),=%"


def check_result_path(rel: object) -> str:
    """A path being READ from a job, which may carry the punctuation `postProcessing` names use.

    The relaxation is narrow on purpose, and the rules that matter are unchanged:

    * only the **segment directly after** ``postProcessing/`` may contain the punctuation — a
    file deeper in that tree is written by OpenFOAM and has an ordinary name;
    * ``..`` is still refused as a segment, and a leading ``/`` with it;
    * **containment on the resolved path still applies**, which is the check that actually stops an
      escape — a symlinked directory walked through is refused by `job_path` whatever this function
      allows.

    So this widens *which names are spellable*, not *where a path may reach*.
    """
    if (
        not isinstance(rel, str)
        or not rel.startswith(POSTPROCESSING_PREFIX)
        or ".." in rel.split("/")
    ):
        return check_relative(rel)
    segments = rel.split("/")
    if len(segments) < 2 or not segments[1]:
        raise JobPathError(f"{rel!r} is not a path inside the job")
    for segment in segments[2:]:
        if not segment:
            raise JobPathError(f"{rel!r} has an empty component")
    # Every segment except the function name must be a plain name; the function name may carry the
    # punctuation OpenFOAM itself put there.
    for segment in (segments[0], *segments[2:]):
        if not REL_PATH_RE.match(segment):
            raise JobPathError(f"{rel!r} has a component that is not a plain name: {segment!r}")
    name = segments[1]
    if not name or any(ch in name for ch in "/\\"):
        raise JobPathError(f"{rel!r} has an unusable function name")
    if re.search(r"%(?![0-9A-F]{2})", name):
        raise JobPathError(f"{rel!r} has an invalid encoded function name")
    if not name.replace(".", "").replace("_", "").replace("-", "").isalnum() and not all(
        ch.isalnum() or ch in _POST_PUNCTUATION or ch in "._-" for ch in name
    ):
        raise JobPathError(f"{rel!r} has a function name with unexpected characters: {name!r}")
    return rel


def result_path(job_dir: Path, rel: object) -> Path:
    """`check_result_path` plus the same containment a staged path gets."""
    checked = check_result_path(rel)
    target = job_dir / checked
    if not target.resolve().is_relative_to(job_dir.resolve()):
        raise JobPathError(f"{checked!r} resolves outside the job folder")
    return target


def has_symlink(root: Path, rel: str) -> bool:
    """Whether ``rel`` or any component of it is a symlink.

    Used before a copy, because `shutil.copytree(symlinks=False)` **follows** a link and copies
    what it points at — which is how a copy out of a job folder becomes a read of something else
    entirely.
    """
    current = root
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            return True
    return False


def tree_bytes(path: Path) -> int:
    """The size of a file or the total size of a folder, for a copy limit."""
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total
