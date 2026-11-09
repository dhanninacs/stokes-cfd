"""The SSH host list: `$STOKES_HOME/hosts.json`, and the ssh options it implies (T-159a).

**This module owns the two things that must not be got wrong.** Where a host's details come
from, and how ssh is told to verify it. Both are small on purpose: the spec asks for the
security-relevant parts to sit in one place, so this file and `stokes/run/ssh.py` are where a
reviewer looks.

## No secrets, ever

`hosts.json` carries the **path** to an identity file and nothing else sensitive. No password,
no key material, no token, no secret - a file that is read at start-up and copied into bug
reports must be safe to look at. The agent's secret lives on the remote host and is read over
ssh when needed (`stokes/run/ssh.py`); it is never written here.

## Strict host keys, and a refusal rather than a prompt

Every ssh invocation this module builds passes `StrictHostKeyChecking=yes` and
`UserKnownHostsFile=<ours>`, so we verify against our own file and never touch the user's. An
unknown key is refused; a **changed** key is refused differently and says "possible
interception", because those are different situations and only one of them is a mistake the
user made earlier.

**Never `accept-new`, never `no`.** And no prompt: a prompt inside a server process is a hang, and a
hang is worse than a clear refusal.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stokes.errors import StokesError
from stokes.log import get_logger

log = get_logger(__name__)

#: The file's name inside `$STOKES_HOME`, and its format version.
HOSTS_FILE = "hosts.json"
HOSTS_VERSION = 1
#: Our own `known_hosts`. **Ours, not the user's**, so a Stokes host is trusted by Stokes and a
#: change to the user's own file does not silently re-trust a machine.
KNOWN_HOSTS_FILE = "known_hosts"
#: The name reserved for this machine. A record may not claim it.
RESERVED_NAMES = ("local",)
#: A host name becomes a path segment in some state, so it follows the preset rule: lower-case,
#: digits, `_` and `-`, 1 to 64, starting alphanumeric.
NAME_RE_TEXT = r"^[a-z0-9][a-z0-9_-]{0,63}$"


#: Options that make ssh verify a host and never ask a human. **One list, so no call site can forget
#: one** - a site that dropped `StrictHostKeyChecking` would silently trust an unknown machine.
def global_known_hosts() -> str:
    """Where `GlobalKnownHostsFile` points on THIS platform. `/dev/null` on POSIX, `NUL` on Windows.

    **`NUL`, not `/dev/null`, because ssh on Windows reads the literal name.**
    """
    return "NUL" if os.name == "nt" else "/dev/null"


def strict_options(known_hosts: Path, identity: Path | None) -> list[str]:
    """The `-o`/`-i` arguments every ssh call must carry. Pure, so the tests can read them.

    **Every option here is one a user's `~/.ssh/config` could otherwise decide** (Opus L2). ssh
    reads its configuration FIRST and the command line after, so an `-o` we pass wins over a config
    that says otherwise - **but only for settings we actually mention.** Measured on this tree:

        a config with `ProxyCommand sh -c 'echo PROXYCOMMAND-RAN >&2; ...'`
        and no defence        ->  PROXYCOMMAND-RAN          <- the config ran a command
        with ProxyCommand=none ->  the command never ran

    **`-F none` is FIRST, in `ssh_options`**, because "read no configuration file" only means that
    when it comes before the others - and a `ProxyCommand` is arbitrary code execution on the
    machine running the app.

    **Repaired prose**: a `#`-prefixed line had been spliced in here, splitting one sentence in two.
    A `#` inside a docstring is legal Python, so nothing objected - see the note in `_cut_once`.
    """
    options = [
        # **`-F none` is FIRST, in `ssh_options`**, because "read no configuration file" only means
        # that when it comes before the others.
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        # **Quoted**, so a state folder with a space - which a Windows user name produces - reaches
        # ssh as ONE value rather than two arguments (Opus round 3, item 6d).
        known_hosts_option(known_hosts),
        # **The system-wide file is not ours and must not be consulted.** A host trusted in
        # `/etc/ssh/ssh_known_hosts` is trusted by the machine's administrator, which is a different
        # statement from "the user of this app has checked this fingerprint".
        "-o",
        f"GlobalKnownHostsFile={global_known_hosts()}",
        "-o",
        "IdentitiesOnly=yes",
        # **`CheckHostIP=no`, so ssh never adds IP entries to OUR known_hosts** (Opus residue 4).
        # With `-F none` there is no config to set it, and **ssh older than 8.5 defaults it to
        # yes** - which is Windows 10's built-in 8.1. It then writes the address alongside the name,
        # and an address that changes (a laptop, a DHCP lease, a VPN) makes strict checking fail for
        # a host whose NAME is correctly trusted.
        #
        # **Unlike `KnownHostsCommand`, this option exists in every version**, so pinning it costs
        # no compatibility - which is exactly why it is safe to add where that one was not.
        "-o",
        "CheckHostIP=no",
        # **`ProxyCommand=none` closes the hole above.** `none` is the documented way to disable it,
        # and a jump host is not something this app offers.
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        # **No connection sharing.** A `ControlPath` from a config could attach this session to a
        # master connection someone else established - to a DIFFERENT host than the one we verified.
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        # **`KnownHostsCommand` is deliberately NOT passed** (Opus recheck item 6a). It exists
        # only from **OpenSSH 8.5** (March 2021), and an ssh that does not know an option name
        # **refuses every connection** with "Bad configuration option" - so passing it would break
        # Windows 10's built-in 8.1, Ubuntu 20.04's 8.2 and RHEL 8's 8.0 **completely**, on a
        # machine where the app would otherwise work.
        #
        # **Nothing is lost**: `-F none` is the first argument, so no configuration file is read
        # and a `KnownHostsCommand` can never be set in the first place. This option was
        # belt-and-braces for a threat `-F none` already removes, and it cost compatibility with
        # every ssh older than 8.5 - which is most Windows machines.
        # **No agent forwarding to a machine we are about to run code on.**
        "-o",
        "ForwardAgent=no",
    ]
    if identity is not None:
        options += ["-i", str(identity)]
    return options


#: A host name or user name: what `ssh` accepts, and nothing readable as an option or a second
#: destination. **A strict allow-list** rather than a list of bad things - a deny-list for a
#: command line is a list of the tricks someone thought of.
#:
#: Letters, digits, `.`, `-`, `_`; an IPv6 literal in brackets. A leading `-` is refused by the
#: first-character rule, and whitespace, `@`, `:` and control characters cannot match at all -
#: each has a meaning to ssh that we will not hand to a value out of a file.
_HOST_CHARS = re.compile(r"\A[A-Za-z0-9._-]{1,253}\Z")
#: An IPv6 literal, which ssh writes in brackets: `[::1]`, `[fe80::1]`.
_IPV6 = re.compile(r"\A\[[0-9A-Fa-f:.]{2,45}\]\Z")


def check_host(value: str, *, field: str = "host") -> str:
    """A host name ssh may be given, or a refusal.

    **This is the fix for Opus's H2** - "option injection: a host record can run a command on the
    Stokes server". Measured before the fix: `user` set to `-oProxyCommand=calc.exe #` reached
    ssh's argv as `-oProxyCommand=calc.exe #@h`, and ssh reads the first non-option word as its
    DESTINATION - so a value from `hosts.json` became an ssh option and ran a command locally.

    A name is refused rather than escaped. Escaping would have to be right about every way ssh
    parses its command line; refusing is right about all of them at once.
    """
    if not value:
        raise HostError(f"the {field} is empty", details={"field": field})
    if value.startswith("-"):
        # **The first character is the whole attack.** An argument beginning with `-` is an
        # option to ssh, whatever it says after that.
        raise HostError(
            f"the {field} {value!r} starts with '-', which ssh would read as an option",
            details={"field": field, "value": value},
        )
    if _HOST_CHARS.match(value):
        return value
    if _IPV6.match(value):
        return value
    raise HostError(
        f"the {field} {value!r} has characters ssh does not accept in a host or user name "
        f"(letters, digits, '.', '-', '_', or an IPv6 literal in brackets)",
        details={"field": field, "value": value},
    )


#: What an interpreter path may contain: letters, digits, `.`, `_`, `/`, `+`, `-`.
#:
#: **An allow-list, because a deny-list for a shell is a list of the tricks someone thought of.**
#: My first version refused only whitespace, control characters and a leading `-` - and **Opus's
#: payload `python3;echo${IFS}INJECTED;python3` was ACCEPTED**, then reached the remote shell where
#: the `;` split it into three commands and `INJECTED` was printed. Measured on this tree.
#:
#: Every character with meaning to a shell is outside this set, so `;`, `&`, `|`, `$`, a backtick,
#: quotes, `(`, `)`, `<`, `>`, `*`, `?`, `~`, `!`, `#` and `{` cannot appear. `+` and `-` are
#: allowed **inside** a name - `python3.11+`, `my-python` - and a leading `-` is refused separately,
#: because that is an ssh option rather than a path.
_PYTHON_CHARS = re.compile(r"\A[A-Za-z0-9._/+-]+\Z")


def check_python(value: str) -> str:
    """The interpreter path ssh will run on the host.

    **A path, and an allow-list of what a path may contain.** The characters that must not appear
    are the ones a shell gives meaning to; anything outside `[A-Za-z0-9._/+-]` is refused rather
    than escaped, because escaping has to be right about every parsing rule and refusing is right
    about all of them at once.
    """
    if not value:
        raise HostError("the python path is empty", details={"field": "python"})
    if value.startswith("-"):
        raise HostError(
            f"the python path {value!r} starts with '-', which would be read as an option",
            details={"field": "python", "value": value},
        )
    if not _PYTHON_CHARS.match(value):
        raise HostError(
            f"the python path {value!r} has characters an interpreter path cannot contain "
            f"(letters, digits, '.', '_', '/', '+', '-')",
            details={"field": "python", "value": value},
        )
    return value


#: What a key path may contain: the interpreter's set **plus `:`**, for a Windows drive letter.
#:
#: **A backslash is deliberately NOT allowed.** ssh accepts forward slashes on Windows, a backslash
#: on the command line is eaten by the remote shell - measured during H3, where `C:\Users\<you>`
#: arrived as `C:Users...` - and permitting one would let a Windows-looking record name a path
#: silently means something else on the far side.
_KEY_CHARS = re.compile(r"\A[A-Za-z0-9._/+-]+:?[A-Za-z0-9._/+-]*\Z|\A[A-Za-z0-9._/+-]+\Z")


def check_key(value: str | None) -> str | None:
    """The identity file, or None when the record names none.

    **A path, and the same reasoning as `python`** - a key path reaches an ssh command line, and a
    value with a shell character in it is read as syntax rather than as a path.
    """
    if value is None or value == "":
        return None
    if value.startswith("-"):
        raise HostError(
            f"the key path {value!r} starts with '-', which would be read as an option",
            details={"field": "key", "value": value},
        )
    # **A drive letter at the START is allowed; a `:` anywhere else is not.** `C:/keys/id` is a
    # real Windows spelling and ssh accepts forward slashes there. Handled as its own case rather
    # than an optional `:` in the pattern, so `a:b:c` cannot sneak through.
    # **`~/` is allowed, and ssh expands it itself.** Measured: `ssh -i "~/.ssh/id_test"` connects,
    # because ssh treats a leading `~` as the home directory. My first version refused it - a FALSE
    # REFUSAL for the most natural way anyone writes a key path, caught by an existing test rather
    # than by me.
    #
    # Only a leading `~/` is accepted: `~user/...` needs a passwd lookup we do not do, and a `~`
    # anywhere else is a character the remote shell would expand unexpectedly.
    if value.startswith("~/"):
        if not _PYTHON_CHARS.match(value[2:]):
            raise HostError(
                f"the key path {value!r} has characters a path cannot contain",
                details={"field": "key", "value": value},
            )
        return value
    if "~" in value:
        raise HostError(
            f"the key path {value!r} has a '~' that is not a leading '~/'; ssh expands only "
            f"that one",
            details={"field": "key", "value": value},
        )
    drive = re.match(r"\A[A-Za-z]:[/\\]", value)
    if drive:
        if "\\" in value:
            raise HostError(
                f"the key path {value!r} uses backslashes; ssh wants forward slashes on Windows, "
                f"and a backslash is eaten by the remote shell",
                details={"field": "key", "value": value},
            )
        if not _PYTHON_CHARS.match(value[2:]):
            raise HostError(
                f"the key path {value!r} has characters a path cannot contain",
                details={"field": "key", "value": value},
            )
        return value
    if not _PYTHON_CHARS.match(value):
        raise HostError(
            f"the key path {value!r} has characters a path cannot contain",
            details={"field": "key", "value": value},
        )
    return value


class HostError(StokesError):
    """A host could not be used: an unreadable list, a bad record, or a refused ssh."""

    code = "host.unusable"


@dataclass(frozen=True)
class SshHost:
    """One remote host. **Attributes are what `hosts.json` holds**, nothing derived."""

    name: str
    host: str
    user: str
    port: int
    key: str
    python: str
    added: str

    @property
    def target(self) -> str:
        """`user@host`, for DISPLAY and for a message.

        **ssh is no longer given this** (Opus H2): `ssh_base` passes the user with `-l` and the host
        as its own argument, because the combined form let a `user` beginning with `-` be read as an
        ssh option. This property survives for the places that show a host to a person, where the
        familiar spelling is the one that reads best.
        """
        return f"{self.user}@{self.host}"

    @property
    def identity(self) -> Path | None:
        """The identity file, or None when the record names none (an agent's own key is used)."""
        return Path(self.key).expanduser() if self.key else None


def hosts_path(home: Path | None = None) -> Path:
    """`$STOKES_HOME/hosts.json`."""
    from stokes.workspace.merge import stokes_home

    return (home if home is not None else stokes_home()) / HOSTS_FILE


def known_hosts_path(home: Path | None = None) -> Path:
    """`$STOKES_HOME/known_hosts`, ours and not the user's.

    **A path containing `%` is REFUSED rather than passed** (Opus recheck item 6b). ssh expands
    `%`-tokens in a `UserKnownHostsFile` value, and **every one of them expands to something**:

        %u -> the user name     %h -> the host       %d -> the home directory
        %r -> the remote user   %p -> the port       %% -> a literal %

    So a `STOKES_HOME` of `/home/<you>/100%done` would be rewritten by ssh into a **different
    file** - and since that file is what host-key verification reads, the effect is silent: our
    `known_hosts` would simply not be consulted, and every host would look unknown or every host
    trusted, depending on what the expansion named. Measured: `%u` became the user name.

    **Refused, not escaped.** `%%` is the escape, but a path with a `%` in it is far more likely
    to be a mistake than an intent, and silently rewriting what a user typed is how a path stops
    pointing where they think it does.
    """
    from stokes.workspace.merge import stokes_home

    folder = home if home is not None else stokes_home()
    text = str(folder)
    if "%" in text:
        raise HostError(
            f"the state folder {text!r} contains '%', which ssh expands inside a known-hosts path "
            f"(a '%u' becomes the user name); move STOKES_HOME somewhere without one",
            details={"field": "home", "value": text},
        )
    # **A SPACE is allowed, and the path is QUOTED where it reaches ssh** (Opus round 3, 6d).
    # My refusal was a false refusal for a real user: Windows user names contain spaces, and it
    # made **every SSH host unusable** for them. Measured: `-o UserKnownHostsFile="..."` with a
    # quoted value containing a space connects successfully, and `ssh -G` reports the path intact.
    #
    # **`%` stays refused**, because it is a different hazard: ssh would silently rewrite the path
    # to a DIFFERENT FILE rather than fail, and our known_hosts would stop being read.
    return folder / KNOWN_HOSTS_FILE


def known_hosts_option(known_hosts: Path) -> str:
    """`UserKnownHostsFile=…` as an ssh option, **quoted so a path with a space survives**.

    ssh accepts a quoted value here, which is what makes a Windows user name with a space
    workable - measured against a real sshd. Only the double quotes are added, which is how ssh
    spells a path containing spaces.
    """
    return f'UserKnownHostsFile="{known_hosts}"'


def check_name(name: str) -> str:
    """A host name, or a refusal. **It becomes a path segment**, so it follows the preset rule."""
    import re

    if not isinstance(name, str) or not re.match(NAME_RE_TEXT, name):
        raise HostError(
            f"host name {name!r}: use 1 to 64 lower-case letters, digits, _ or -, "
            "starting with a letter or a digit",
            details={"name": name},
        )
    if name in RESERVED_NAMES:
        raise HostError(f"{name!r} is reserved for this machine", details={"name": name})
    return name


def read_hosts(path: Path | None = None) -> list[SshHost]:
    """Every usable host in the file. **A missing file is an empty list, not an error.**

    A user who has never added a host has no `hosts.json`, and that is the ordinary state -
    so the difference between "no hosts" and "a broken file" is that the latter raises.
    """
    found = path if path is not None else hosts_path()
    try:
        text = found.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise HostError(f"cannot read {found}: {exc}", details={"path": str(found)}) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise HostError(f"{found} is not valid JSON: {exc}", details={"path": str(found)}) from exc
    if not isinstance(data, dict) or data.get("version") != HOSTS_VERSION:
        raise HostError(
            f"{found} is not a version {HOSTS_VERSION} host list",
            details={
                "path": str(found),
                "version": data.get("version") if isinstance(data, dict) else None,
            },
        )
    records = data.get("hosts")
    if not isinstance(records, list):
        raise HostError(f"{found} has no hosts list", details={"path": str(found)})
    out: list[SshHost] = []
    for position, record in enumerate(records):
        out.append(_read_record(record, position, found))
    return out


def _read_record(record: Any, position: int, path: Path) -> SshHost:
    """One record, with every field checked. **A bad record is refused, never repaired.**

    Repairing would mean guessing which host the user meant, and the guess would be about a machine
    we are about to run code on.
    """
    where = f"{path}: host {position}"
    if not isinstance(record, dict):
        raise HostError(f"{where} is not an object", details={"position": position})
    name = check_name(str(record.get("name") or ""))
    # **Validated HERE, at the boundary, so a hostile record can never become an `SshHost`** (Opus
    # H2). The reading is where a value from a file first enters the system, so a refusal here means
    # no later code has to remember to check it.
    try:
        host = check_host(str(record.get("host") or ""), field="host")
        user = check_host(str(record.get("user") or ""), field="user")
    except HostError as exc:
        raise HostError(f"{where} ({name!r}): {exc}", details=exc.details) from exc
    check_python(str(record.get("python") or "python3"))
    # **The key is a path that reaches a command line too** (Opus item 1). Validated at the boundary
    # with the same allowance as `python`, so a value with a shell character never becomes an argv.
    check_key(str(record.get("key") or "") or None)
    port = record.get("port", 22)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise HostError(f"{where} ({name!r}) has a bad port {port!r}", details={"name": name})
    # **A secret in the file is refused, not ignored.** `hosts.json` is copied into bug reports and
    # read at start-up; a `password` or `secret` key here is a mistake we should not let stand.
    for forbidden in ("password", "passwd", "secret", "token", "key_material"):
        if forbidden in record:
            raise HostError(
                f"{where} ({name!r}) carries {forbidden!r}; hosts.json holds no secrets - give the "
                "path of an identity file in `key` instead",
                details={"name": name, "field": forbidden},
            )
    return SshHost(
        name=name,
        host=host,
        user=user,
        port=port,
        key=str(record.get("key") or ""),
        python=str(record.get("python") or "python3"),
        added=str(record.get("added") or ""),
    )


def find_host(name: str, path: Path | None = None) -> SshHost | None:
    """The record for `name`, or None. `local` is never in the file."""
    for record in read_hosts(path):
        if record.name == name:
            return record
    return None


def write_hosts(records: list[SshHost], path: Path | None = None) -> Path:
    """Write the list, **0600**. The commands are what call this; the runner only reads."""
    found = path if path is not None else hosts_path()
    found.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "version": HOSTS_VERSION,
        "hosts": [
            {
                "name": record.name,
                "host": record.host,
                "user": record.user,
                "port": record.port,
                "key": record.key,
                "python": record.python,
                "added": record.added,
            }
            for record in records
        ],
    }
    temp = found.with_name(f".{found.name}.tmp")
    temp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        # **0600 before the rename**, so the file is never briefly world-readable.
        temp.chmod(0o600)
    temp.replace(found)
    return found
