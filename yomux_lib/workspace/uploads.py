"""Fixture attribution for the retired Python upload location."""

from __future__ import annotations

import os
from pathlib import Path


UPLOAD_TMP_BASE = Path("/tmp") / f"yolomux-server-{os.getuid()}" / "shared"
