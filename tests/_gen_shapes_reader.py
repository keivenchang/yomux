"""Step 2 of regenerating tests/fixtures/stats_request_shapes.json.

Fills each golden case's ``readerRequest`` — the statsd `_encoded_history`
request dict — via the REAL web-layer translator through the contract-tested
mirror (`tests.browser_helpers.stats_request_shapes.reader_history_request`),
never a hand-mapped JS mirror. Run after ``cd tests && node _gen_shapes.js``:

    python3 tests/_gen_shapes_reader.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.browser_helpers.stats_request_shapes import reader_history_request

FIXTURE = Path(__file__).parent / "fixtures" / "stats_request_shapes.json"


def main() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    now = int(payload["nowSeconds"])
    for case in payload["cases"]:
        if case["name"] == "history-suppressed-backoff":
            case["readerRequest"] = None
            continue
        case["readerRequest"] = reader_history_request(int(case["rangeSeconds"]), now, client_id="golden-client")
    FIXTURE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"readerRequest filled for {len(payload['cases'])} cases")


if __name__ == "__main__":
    main()
