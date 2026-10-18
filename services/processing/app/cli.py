"""Run a real BatchCreated payload through the processing core, no broker needed.

psql ... -Atc "select payload from outbox where event_type='BatchCreated' limit 1" | python -m app.cli
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import TextIO

from .pipeline import process_batch
from .pseudonymize import Pseudonymizer


def main(argv: list[str] | None = None, stdin: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process one BatchCreated payload read from stdin")
    parser.add_argument("--json", action="store_true", help="print the full silver output as JSON")
    args = parser.parse_args(argv)

    payload = json.loads((stdin or sys.stdin).read())
    result = process_batch(payload, Pseudonymizer.from_env())
    if args.json:
        print(result.model_dump_json(indent=2))
        return 0

    print(f"batch {result.batch_id} from {result.source}: {result.summary()}")
    reasons = Counter(f"{i.field}:{i.code}" for r in result.rejects for i in r.issues)
    for reason, n in reasons.most_common():
        print(f"  rejected {n}x  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
