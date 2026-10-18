"""Fixed historical route metadata for fixture lifecycle safety tests; no request handlers."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from types import SimpleNamespace

RESPONSE_SSE = "sse"
RESPONSE_WEBSOCKET = "websocket"
ALL_ROUTES = tuple(SimpleNamespace(**row) for row in json.loads((Path(__file__).resolve().parents[1] / "fixtures/retired_route_contract.json").read_text())["routes"])


def route_for_request(method: str, path: str) -> SimpleNamespace | None:
    exact = next((route for route in ALL_ROUTES if route.method == method.upper() and route.path == path), None)
    if exact is not None:
        return exact
    return next((route for route in ALL_ROUTES if route.method == method.upper() and (fnmatch.fnmatchcase(path, route.path) or (route.path.endswith("/") and route.path != "/" and path.startswith(route.path)))), None)
