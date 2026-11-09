"""Issue a licence file signed with the private key (ADR-0009).

Example::

    python tools/issue_license.py --holder "Jane Doe" --features remote reports \
        --expires 2027-10-02 --this-machine --out jane.stokes-license.json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stokes.license import FEATURES, machine_fingerprint, sign_payload  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holder", required=True)
    parser.add_argument("--features", nargs="*", default=[], choices=list(FEATURES))
    parser.add_argument("--expires", help="YYYY-MM-DD; omit for perpetual")
    parser.add_argument("--grace-days", type=int, default=14)
    parser.add_argument("--machine", help="fingerprint to bind to")
    parser.add_argument("--this-machine", action="store_true", help="bind to this machine")
    parser.add_argument("--key", default=str(Path.home() / ".stokes" / "signing-key.pem"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    payload = {
        "id": uuid.uuid4().hex[:12],
        "holder": args.holder,
        "product": "stokes",
        "features": sorted(set(args.features)),
        "issued": datetime.now(UTC).date().isoformat(),
        "expires": args.expires,
        "machine": machine_fingerprint() if args.this_machine else args.machine,
        "grace_days": args.grace_days,
    }
    document = sign_payload(payload, Path(args.key).expanduser().read_bytes())
    Path(args.out).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"licence {payload['id']} written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
