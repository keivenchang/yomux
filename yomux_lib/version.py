# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Read the product version from the repository's canonical version manifest."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re


def _read_product_version() -> str:
    manifest = Path(__file__).resolve().parents[1] / "version.yaml"
    if manifest.is_file():
        lines = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
        values = [re.fullmatch(r'version: "(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"', line) for line in lines]
        if len(values) != 1 or values[0] is None:
            raise ValueError(f"expected exactly one version field in {manifest}")
        return ".".join(values[0].groups())
    try:
        return version("yomux")
    except PackageNotFoundError:
        return "0.0.0"


YOMUX_VERSION = _read_product_version()
