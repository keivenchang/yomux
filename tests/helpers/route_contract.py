"""Historical route metadata plus current additions for fixture lifecycle safety tests."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from types import SimpleNamespace

RESPONSE_SSE = "sse"
RESPONSE_WEBSOCKET = "websocket"
ALL_ROUTES = tuple(SimpleNamespace(**row) for row in json.loads((Path(__file__).resolve().parents[1] / "fixtures/retired_route_contract.json").read_text())["routes"])
# Keep the historical migration snapshot intact while admitting the native batch endpoint.
ALL_ROUTES += (SimpleNamespace(method="POST", path="/api/events", role="readonly", protocol="json", body_limit=64 * 1024, group="core"),)


def route_for_request(method: str, path: str) -> SimpleNamespace | None:
    exact = next((route for route in ALL_ROUTES if route.method == method.upper() and route.path == path), None)
    if exact is not None:
        return exact
    return next((route for route in ALL_ROUTES if route.method == method.upper() and (fnmatch.fnmatchcase(path, route.path) or (route.path.endswith("/") and route.path != "/" and path.startswith(route.path)))), None)
