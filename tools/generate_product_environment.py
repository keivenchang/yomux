#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Generate shell namespace constants from the shared definition."""

import argparse
import json
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[1]
START = "# BEGIN GENERATED PRODUCT NAMESPACE\n"
END = "# END GENERATED PRODUCT NAMESPACE\n"


def render_namespace(namespace: dict) -> str:
    return (START + "PRODUCT_ENV_CANONICAL_PREFIX=" + shlex.quote(namespace["canonical"]) + "\n"
            + "PRODUCT_ENV_LEGACY_PREFIXES=(" + " ".join(shlex.quote(prefix) for prefix in namespace["legacy"]) + ")\n" + END)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--sync-installed", action="store_true", help="refresh the installed shared launcher owner")
    args = parser.parse_args()
    namespace = json.loads((ROOT / "tools/product_namespace.json").read_text())
    path = ROOT / "tools/product_environment.sh"
    installed_path = Path.home() / "dev/ai-config/agents/skills/yomux-dev-start/scripts/environment.sh"
    source = path.read_text()
    begin, end = source.index(START), source.index(END) + len(END)
    expected = source[:begin] + render_namespace(namespace) + source[end:]
    go_path = ROOT / "internal/productenv/namespace.json"
    go_source = json.dumps(namespace, separators=(",", ":")) + "\n"
    if args.check:
        if go_path.read_text() != go_source:
            print("product environment Go namespace is stale; run tools/generate_product_environment.py")
            return 1
        if expected != source:
            print("product environment shell namespace is stale; run tools/generate_product_environment.py")
            return 1
        if installed_path.exists() and installed_path.read_text() != source:
            print("installed launcher owner is stale; run tools/generate_product_environment.py --sync-installed")
            return 1
    else:
        if expected != source:
            path.write_text(expected)
        if go_path.read_text() != go_source:
            go_path.write_text(go_source)
        if args.sync_installed:
            installed_path.write_text(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
