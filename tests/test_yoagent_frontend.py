# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_yoagent_expanded_auxiliary_details_do_not_use_inner_scroll():
    css = (ROOT / "static_src/css/yolomux/50_terminal_file_tree.css").read_text(encoding="utf-8")
    rule = re.search(r"\.yoagent-message-details\[open\]\s+pre\.yoagent-auxiliary-stream\s*\{(?P<body>[^}]*)\}", css)

    assert rule is not None
    body = rule.group("body")
    assert "max-height: none" in body
    assert "overflow: visible" in body
