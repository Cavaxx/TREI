"""Write the synthetic demo dataset to disk for inspection.

Usage:
    python scripts/seed_demo.py [out_dir]

The output is clearly labelled synthetic and is intended for local development
and demos only. It is NOT real market data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a plain script: put the repository root on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.seed import generate_demo_auctions, generate_demo_listings  # noqa: E402


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else Path("data")
    out_dir.mkdir(parents=True, exist_ok=True)

    listings = generate_demo_listings()
    auctions = generate_demo_auctions()

    (out_dir / "demo_listings.json").write_text(json.dumps(listings, indent=2))
    (out_dir / "demo_auctions.json").write_text(json.dumps(auctions, indent=2))

    print(f"wrote {len(listings)} listings and {len(auctions)} auctions to {out_dir}/")
    print("NOTE: synthetic demo data - not real market data.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
