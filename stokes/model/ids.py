"""Node identifiers: short, random, stable for the life of a node."""

from __future__ import annotations

import re
import secrets

#: **`\Z`, not `$`** (Opus review item 17). `$` matches before a trailing newline, so
#: `ID_RE.match("r_abc12345\n")` was True - measured - and an id is used as a PATH SEGMENT by
#: `fields_path`, `_record_folder` and the upload target. It was a 404 in practice rather than a
#: traversal, but "an id with a newline in it" is not an id, and the anchor is the one place to say
#: so: every existing `ID_RE.match` call site becomes correct without being touched.
ID_RE = re.compile(r"^[a-z]{1,4}_[0-9a-f]{8}\Z")


def new_id(prefix: str = "n") -> str:
    """A fresh id such as ``n_3fa9c2d1``. Prefixes group ids by role (``p`` project)."""
    return f"{prefix}_{secrets.token_hex(4)}"


def is_id(text: str) -> bool:
    """Whether ``text`` is an id. **`fullmatch`**, so nothing follows the eight hex digits."""
    return isinstance(text, str) and ID_RE.fullmatch(text) is not None


def stable_id(prefix: str, key: str) -> str:
    """A deterministic id for structural nodes (the fixed groups), identical in every project."""
    import hashlib

    digest = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{prefix}_{digest}"
