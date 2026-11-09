"""Generate the Ed25519 licence signing keypair.

The private key is written outside the repository (default
``~/.stokes/signing-key.pem``, mode 600); the public key goes to
``stokes/license/public_key.pem`` and is committed. Release builds swap the
public key for the production one at build time (ADR-0009).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_KEY_PATH = ROOT / "stokes" / "license" / "public_key.pem"


def main(argv: list[str] | None = None) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--private",
        default=str(Path.home() / ".stokes" / "signing-key.pem"),
        help="where to write the private key (outside the repository)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing private key")
    args = parser.parse_args(argv)

    private_path = Path(args.private).expanduser()
    if private_path.exists() and not args.force:
        print(f"refusing to overwrite {private_path}; pass --force")
        return 1
    key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    if os.name != "nt":
        private_path.chmod(0o600)
    PUBLIC_KEY_PATH.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    print(f"private key: {private_path}")
    print(f"public key:  {PUBLIC_KEY_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
