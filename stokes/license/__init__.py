"""Licence files and feature flags (ADR-0009).

The free tier runs with no licence file. Paid features are flags inside an
Ed25519-signed JSON payload. Verification happens before any field is read.
"""

from stokes.license.licensing import (
    FEATURES,
    Features,
    LicenseInfo,
    canonical_bytes,
    load_license,
    machine_fingerprint,
    sign_payload,
    verify_license,
)

__all__ = [
    "FEATURES",
    "Features",
    "LicenseInfo",
    "canonical_bytes",
    "load_license",
    "machine_fingerprint",
    "sign_payload",
    "verify_license",
]
