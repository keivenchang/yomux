#!/usr/bin/env python3
"""Validate and normalize the internal site's captured rates snapshot."""

from __future__ import annotations

import json
import sys
from pathlib import Path


SOURCE_URL = "https://inference-backend.internal/api/v1/cost/rates"
CAPTURED_AT = "2026-09-10T00:00:00Z"


def normalize_model(row: dict[str, object]) -> dict[str, object] | None:
    provider = row.get("provider")
    model = row.get("model")
    if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
        return None
    if all(row.get(key) is None for key in ("price_1m_input_tokens", "price_1m_output_tokens", "price_1m_cache_read_input_tokens")):
        return None
    canonical = model
    prefix = f"{provider}/"
    if canonical.startswith(prefix):
        canonical = canonical[len(prefix):]
    if provider == "openai":
        while canonical.startswith("openai/"):
            canonical = canonical[len("openai/"):]
    aliases = [canonical] if canonical == model else [canonical, model]
    rates = []
    for field, direction, cache_role in (
        ("price_1m_input_tokens", "input", "none"),
        ("price_1m_output_tokens", "output", "none"),
        ("price_1m_cache_read_input_tokens", "input", "read"),
    ):
        value = row.get(field)
        if value is not None:
            rates.append({"direction": direction, "modality": "text", "cache_role": cache_role, "unit": "tokens", "scale": 1000000, "usd": str(value), "effective_from": "1970-01-01T00:00:00Z"})
    return {"provider": provider, "model": canonical, "aliases": aliases, "source": {"url": SOURCE_URL, "kind": "official", "checked_at": CAPTURED_AT}, "rates": rates}


def main() -> int:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "/nfs/keivenc/rates.json")
    destination = Path(sys.argv[2] if len(sys.argv) > 2 else "yolomux_lib/data/internal_model_pricing.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("rates")
    if not isinstance(rows, list) or payload.get("count") != len(rows) or payload.get("priced_count") != sum(any(row.get(key) is not None for key in ("price_1m_input_tokens", "price_1m_output_tokens", "price_1m_cache_read_input_tokens")) for row in rows):
        raise SystemExit("internal rates count metadata does not match payload")
    models = [model for row in rows if isinstance(row, dict) for model in [normalize_model(row)] if model is not None]
    output = {"schema_version": 1, "catalog_revision": 5, "generated_at": CAPTURED_AT, "source_count": len(rows), "source_priced_count": payload["priced_count"], "models": models}
    destination.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"validated {len(rows)} records, {payload['priced_count']} priced; wrote {len(models)} usable models to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
