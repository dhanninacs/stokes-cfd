"""Ed25519 licence files.

File format (JSON)::

    {"stokes_license": 1,
     "payload": {"id": "...", "holder": "...", "product": "stokes",
                 "features": ["remote", "reports"], "issued": "2026-10-02",
                 "expires": "2027-10-02" | null, "machine": "<fingerprint>" | null,
                 "grace_days": 14},
     "signature": "<base64 of Ed25519 signature over canonical payload bytes>"}

The signing key never enters the repository; ``tools/issue_license.py`` uses it.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from stokes.errors import LicenceError, LicenceFeatureMissing

FEATURES: tuple[str, ...] = ("remote", "fluent", "assistant", "reports")
PRODUCT = "stokes"
FORMAT_VERSION = 1
MAX_LICENSE_BYTES = 64 * 1024
_PUBLIC_KEY_FILE = Path(__file__).with_name("public_key.pem")


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """The exact bytes that are signed: sorted keys, no whitespace, UTF-8."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def machine_fingerprint() -> str:
    """A stable, salted identifier of this machine (not reversible)."""
    # Lazy: platform (WMI on Windows), uuid and hashlib cost ~250 ms here and are rarely needed.
    import hashlib
    import platform
    import uuid

    parts = [platform.node(), platform.machine(), platform.system(), f"{uuid.getnode():x}"]
    digest = hashlib.sha256(("stokes-fp-v1|" + "|".join(parts)).encode("utf-8")).hexdigest()
    return digest[:32]


@dataclass(frozen=True)
class LicenseInfo:
    id: str
    holder: str
    features: frozenset[str]
    issued: date
    expires: date | None
    machine: str | None
    grace_days: int
    payload: dict[str, Any] = field(default_factory=dict, compare=False)

    def is_expired(self, today: date | None = None) -> bool:
        if self.expires is None:
            return False
        today = today or datetime.now(UTC).date()
        return today > self.expires + timedelta(days=self.grace_days)


def _load_public_key(pem: bytes | None) -> Any:  # Any: cryptography Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization

    data = pem if pem is not None else _PUBLIC_KEY_FILE.read_bytes()
    key = serialization.load_pem_public_key(data)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(key, Ed25519PublicKey):
        raise LicenceError("the licence public key is not an Ed25519 key")
    return key


def verify_license(
    document: Any, *, public_key_pem: bytes | None = None
) -> LicenseInfo:  # Any: JSON
    """Verify a parsed licence document and return its information."""
    from cryptography.exceptions import InvalidSignature

    if not isinstance(document, dict) or document.get("stokes_license") != FORMAT_VERSION:
        raise LicenceError("not a Stokes licence file")
    payload = document.get("payload")
    signature = document.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise LicenceError("licence file is missing payload or signature")
    try:
        raw_signature = base64.b64decode(signature, validate=True)
    except ValueError as exc:
        raise LicenceError("licence signature is not valid base64") from exc
    key = _load_public_key(public_key_pem)
    try:
        key.verify(raw_signature, canonical_bytes(payload))
    except InvalidSignature as exc:
        raise LicenceError("licence signature does not verify") from exc
    if payload.get("product") != PRODUCT:
        raise LicenceError("licence is for a different product")
    try:
        features = frozenset(str(f) for f in payload.get("features", []))
        issued = date.fromisoformat(str(payload["issued"]))
        expires_raw = payload.get("expires")
        expires = date.fromisoformat(str(expires_raw)) if expires_raw else None
        grace = int(payload.get("grace_days", 0))
    except (KeyError, ValueError, TypeError) as exc:
        raise LicenceError(f"licence payload is malformed: {exc}") from exc
    unknown = sorted(features - set(FEATURES))
    if unknown:
        raise LicenceError(f"licence names unknown features: {', '.join(unknown)}")
    return LicenseInfo(
        id=str(payload.get("id", "")),
        holder=str(payload.get("holder", "")),
        features=features,
        issued=issued,
        expires=expires,
        machine=(str(payload["machine"]) if payload.get("machine") else None),
        grace_days=grace,
        payload=payload,
    )


def load_license(
    path: str | Path, *, public_key_pem: bytes | None = None, check_machine: bool = True
) -> LicenseInfo:
    file = Path(path)
    if file.stat().st_size > MAX_LICENSE_BYTES:
        raise LicenceError("licence file is too large")
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LicenceError(f"cannot read licence file: {exc}") from exc
    info = verify_license(document, public_key_pem=public_key_pem)
    if info.is_expired():
        raise LicenceError(f"licence {info.id} expired on {info.expires}")
    if check_machine and info.machine and info.machine != machine_fingerprint():
        raise LicenceError("licence is bound to a different machine")
    return info


def sign_payload(payload: dict[str, Any], private_key_pem: bytes) -> dict[str, Any]:
    """Produce a licence document (used by tools/issue_license.py and tests)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenceError("the signing key is not an Ed25519 key")
    signature = key.sign(canonical_bytes(payload))
    return {
        "stokes_license": FORMAT_VERSION,
        "payload": payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


class Features:
    """The feature flags available in this session."""

    def __init__(
        self, flags: frozenset[str] | set[str] | None = None, info: LicenseInfo | None = None
    ):
        self.flags: frozenset[str] = frozenset(flags or ())
        self.info = info

    @classmethod
    def free(cls) -> Features:
        return cls()

    @classmethod
    def from_license(cls, info: LicenseInfo) -> Features:
        return cls(info.features, info)

    @classmethod
    def from_environment(cls) -> Features:
        """Load ``STOKES_LICENSE_FILE`` when set and valid; otherwise the free tier."""
        path = os.environ.get("STOKES_LICENSE_FILE")
        if not path:
            return cls.free()
        return cls.from_license(load_license(path))

    def has(self, flag: str) -> bool:
        return flag in self.flags

    def require(self, flag: str) -> None:
        if flag not in self.flags:
            raise LicenceFeatureMissing(
                f"the {flag!r} feature is not licensed in this session", details={"feature": flag}
            )
