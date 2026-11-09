from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from stokes.errors import LicenceError, LicenceFeatureMissing
from stokes.license import Features, load_license, machine_fingerprint, sign_payload, verify_license


def keypair() -> tuple[bytes, bytes]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "abc123",
        "holder": "Test Holder",
        "product": "stokes",
        "features": ["remote", "reports"],
        "issued": "2026-10-02",
        "expires": None,
        "machine": None,
        "grace_days": 14,
    }
    base.update(overrides)
    return base


def test_sign_and_verify() -> None:
    private, public = keypair()
    document = sign_payload(payload(), private)
    info = verify_license(document, public_key_pem=public)
    assert info.features == {"remote", "reports"}
    assert info.holder == "Test Holder" and not info.is_expired()


def test_tampering_fails() -> None:
    private, public = keypair()
    document = sign_payload(payload(), private)
    document["payload"]["features"] = ["fluent", "remote", "reports"]
    with pytest.raises(LicenceError):
        verify_license(document, public_key_pem=public)
    _, other_public = keypair()
    with pytest.raises(LicenceError):
        verify_license(sign_payload(payload(), private), public_key_pem=other_public)


def test_expiry_and_grace(tmp_path: Path) -> None:
    private, public = keypair()
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    expired = tmp_path / "expired.json"
    expired.write_text(json.dumps(sign_payload(payload(expires=yesterday, grace_days=0), private)))
    with pytest.raises(LicenceError):
        load_license(expired, public_key_pem=public)
    graced = tmp_path / "graced.json"
    graced.write_text(json.dumps(sign_payload(payload(expires=yesterday, grace_days=30), private)))
    assert load_license(graced, public_key_pem=public).features == {"remote", "reports"}


def test_machine_binding(tmp_path: Path) -> None:
    private, public = keypair()
    bound = tmp_path / "bound.json"
    bound.write_text(json.dumps(sign_payload(payload(machine="deadbeef"), private)))
    with pytest.raises(LicenceError):
        load_license(bound, public_key_pem=public)
    assert load_license(bound, public_key_pem=public, check_machine=False).machine == "deadbeef"
    mine = tmp_path / "mine.json"
    mine.write_text(json.dumps(sign_payload(payload(machine=machine_fingerprint()), private)))
    assert load_license(mine, public_key_pem=public).machine == machine_fingerprint()


def test_bad_documents() -> None:
    private, public = keypair()
    with pytest.raises(LicenceError):
        verify_license({"not": "a licence"}, public_key_pem=public)
    with pytest.raises(LicenceError):
        verify_license(sign_payload(payload(product="other"), private), public_key_pem=public)
    with pytest.raises(LicenceError):
        verify_license(sign_payload(payload(features=["teleport"]), private), public_key_pem=public)


def test_features() -> None:
    free = Features.free()
    assert not free.has("remote")
    with pytest.raises(LicenceFeatureMissing):
        free.require("remote")
    Features({"remote"}).require("remote")


def test_committed_public_key_matches_dev_signing_key_when_present(tmp_path: Path) -> None:
    dev_key = Path.home() / ".stokes" / "dev-signing-key.pem"
    if not dev_key.is_file():
        pytest.skip("no dev signing key on this machine")
    document = sign_payload(payload(), dev_key.read_bytes())
    assert verify_license(document).features == {"remote", "reports"}
