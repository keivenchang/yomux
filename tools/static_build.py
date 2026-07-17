# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Build served YOLOmux static assets from ordered source partials."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.locales import FALLBACK_LOCALE
from yolomux_lib.locales import PLURAL_CATEGORIES_BY_LOCALE
from yolomux_lib.locales import PSEUDO_LOCALE
from yolomux_lib.locales import SHIPPED_LOCALES

# i18n: source catalogs live in static_src/locales/<locale>.json; en.json is the source of truth.
# The build copies them to static/locales/ (all-static-fetch delivery), validates key parity, and
# generates the en-XA pseudo-locale (accented + padded) to surface unextracted strings + overflow.
LOCALES_SRC = REPO_ROOT / "static_src" / "locales"
LOCALES_OUT = REPO_ROOT / "static" / "locales"
SOURCE_LOCALE = FALLBACK_LOCALE
WINDOW_VIEWPORT_ALLOW_MARKER = "static-build-allow-window-viewport"
RAW_TOKEN_LITERAL_IGNORED_VALUES: set[str] = set()
RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST: dict[str, str] = {}
# Existing component-owned colors are an explicit migration baseline. New literals must instead
# use a shared token or be added here with a reviewed reason; normalized identities make equivalent
# hex/rgb spellings share one entry.
NOVEL_COMPONENT_COLOR_ALLOWLIST: dict[str, tuple[frozenset[str], str]] = {
    "static_src/css/yolomux/10_topbar_menus.css": (
        frozenset({
            "#041108", "rgb(255 255 255 / 0.42)", "rgb(255 255 255 / 0.66)",
        }),
        "reviewed pre-token component palette (2026-07-07)",
    ),
    "static_src/css/yolomux/20_sessions_popovers.css": (
        frozenset({
            "#082014", "#10151f", "#3a3524", "#67d7ff", "#7a1020", "#9befad",
            "#a5e8ff", "#aab4c4", "#e7ebf1", "#ff304a", "#ffd75d", "#ffd7dc",
            "rgb(255 226 122 / 0.62)", "rgb(255 226 122 / 0.92)",
            "rgb(255 255 255 / 0.07)", "rgb(255 255 255 / 0.24)",
            "rgb(255 255 255 / 0.62)",
        }),
        "reviewed pre-token component palette (2026-07-07)",
    ),
    "static_src/css/yolomux/30_preferences_changes.css": (
        frozenset({
            "#081205", "#a6e35f", "rgb(174 184 199 / 0.62)",
            "rgb(255 255 255 / 0.18)", "rgb(48 57 72 / 0.45)",
            "rgb(82 95 116 / 0.42)",
        }),
        "reviewed pre-token component palette (2026-07-07)",
    ),
    "static_src/css/yolomux/40_layout_panes_tabs.css": (
        frozenset({
            "#7a8799", "rgb(0 0 0 / 0.52)", "rgb(0 0 0 / 0.66)",
            "rgb(8 18 5 / 0.28)", "rgb(245 197 66 / 0.95)",
            "rgb(255 255 255 / 0.16)",
        }),
        "reviewed pre-token component palette (2026-07-07)",
    ),
    "static_src/css/yolomux/50_terminal_file_tree.css": (
        frozenset({
            "#14171d", "#2a1e00", "#314154",
            "#3a2b00", "#3b82f6", "#6f7b8d", "#6fce8a", "#b98c24",
            "#c8941e", "#d28b00", "#e8b04b", "#ffe7a3", "#fff3c6",
            "#fff4c2", "rgb(0 0 0 / 0.35)", "rgb(112 167 255 / 0.65)",
            "rgb(226 232 240 / 0.16)", "rgb(255 102 115 / 0.14)",
            "rgb(255 243 198 / 0.72)", "rgb(255 244 194 / 0.58)",
            "rgb(255 255 255 / 0.26)",
        }),
        "reviewed pre-token component palette (2026-07-07)",
    ),
    "static_src/css/yolomux/60_editor_file_panels.css": (
        frozenset({
            "#0b0e14", "#0e1724", "#0f131b", "#17210f", "#202838", "#35d36f",
            "#3b0a0a", "#59677d", "#6cb6df", "#91a68a", "#9b2d33", "#a8e95a",
            "#b9e7c2", "#b9e9ff", "#c69a15", "#c9ff7a", "#d9ffe0", "#edf3ff",
            "#f4b7b7", "#f6e6a8", "#ff5a6d", "#ffe0e4", "#fff8cc",
            "rgb(0 0 0 / 0.2)", "rgb(0 0 0 / 0.24)",
            "rgb(120 180 255 / 0.4)", "rgb(159 216 255 / 0.1)",
            "rgb(159 216 255 / 0.12)", "rgb(159 216 255 / 0.14)",
            "rgb(159 216 255 / 0.22)", "rgb(177 40 55 / 0.26)",
            "rgb(180 230 140 / 0.34)", "rgb(201 255 122 / 0.08)",
            "rgb(226 238 248 / 0.045)", "rgb(226 238 248 / 0.18)",
            "rgb(245 197 66 / 0.55)", "rgb(255 255 255 / 0.025)",
            "rgb(255 255 255 / 0.03)", "rgb(255 255 255 / 0.045)",
            "rgb(255 255 255 / 0.12)",
            "rgb(28 146 68 / 0.24)", "rgb(9 105 218 / 0.04)",
        }),
        "reviewed pre-token component palette (2026-07-07)",
    ),
}

# These definitions predate the unused-token gate. Keep them visible and individually reviewable so
# new dead tokens fail while follow-up cleanup can remove entries one by one.
UNUSED_CSS_TOKEN_ALLOWLIST: dict[str, str] = {
    token: "reviewed pre-lint unused token baseline (2026-07-07)"
    for token in (
        "--active-ring", "--agent-window-activity-block-size",
        "--agent-window-activity-inline-size", "--attention-ring-border", "--auto-active-text",
        "--auto-glow", "--auto-muted-text",
        "--auto-surface", "--auto-surface-active", "--bottom", "--changes-folder-bg",
        "--changes-folder-text", "--changes-icon-text", "--changes-indent-line",
        "--changes-pane-min-inline-size", "--changes-path-text", "--changes-row-hover-bg",
        "--cm-search-field-gap", "--dense-control-height", "--dense-control-min-height",
        "--drop-outline-bg", "--drop-outline-strong", "--drop-outline-surface",
        "--dv-active-sash-color", "--dv-activegroup-hiddenpanel-tab-background-color",
        "--dv-activegroup-hiddenpanel-tab-color",
        "--dv-activegroup-visiblepanel-tab-background-color",
        "--dv-activegroup-visiblepanel-tab-color", "--dv-drag-over-background-color",
        "--dv-drag-over-border", "--dv-drag-over-border-color",
        "--dv-group-view-background-color",
        "--dv-inactivegroup-hiddenpanel-tab-background-color",
        "--dv-inactivegroup-hiddenpanel-tab-color",
        "--dv-inactivegroup-visiblepanel-tab-background-color",
        "--dv-inactivegroup-visiblepanel-tab-color", "--dv-sash-color", "--dv-tab-divider-color",
        "--dv-tabs-and-actions-container-background-color",
        "--dv-tabs-and-actions-container-font-size", "--dv-tabs-and-actions-container-height",
        "--editor-text-padding-inline", "--editor-ui-font-size", "--editor-ui-font-size-sm",
        "--file-explorer-open-inline-size", "--inactive-gray", "--inactive-gray-hover",
        "--inactive-tab-bg", "--inactive-tab-border", "--info-tree-border",
        "--info-tree-group-bg", "--left",
        "--pane-detail-bg", "--pane-tab-active-accent", "--pane-tab-panel-detail-bg",
        "--pane-tab-panel-head-bg", "--pane-tab-panel-ring-shadow", "--pane-tab-zoom-bg",
        "--pane-tab-zoom-border", "--pane-tab-zoom-hover-bg", "--pane-tab-zoom-hover-border",
        "--panel2-inactive", "--pc-zoom-bg", "--pc-zoom-border", "--pc-zoom-hover-bg",
        "--pc-zoom-hover-border", "--red-reminder-easing", "--right", "--top",
        "--yolo-reminder-duration", "--z-finder-quick-access", "--z-tab-popover",
    )
}
UNUSED_CSS_TOKEN_ALLOWLIST["--js-debug-agent-token-pattern-ink-rgb"] = (
    "established YO!stats theme token retained for renderer compatibility"
)
SEMANTIC_CONTRAST_PAIRS: tuple[tuple[str, str], ...] = (
    ("--text", "--bg"),
    ("--pane-tab-text", "--pane-tab-control-bg"),
    ("--pane-tab-active-text", "--pane-tab-active-bg"),
)
SHARED_UI_OWNERSHIP_REQUIREMENTS = {
    "static_src/js/yolomux/99_terminal_boot.js": (
        ("pane-frame controls", "function paneFrameControlsHtml", "toolbarButtonHtml("),
    ),
    "static_src/js/yolomux/20_layout_state.js": (
        ("editor state", "function applyEditorStateFields", "applyEditorStateFields("),
    ),
    "static_src/js/yolomux/97_share_replay.js": (
        ("shared editor replay", "applyEditorStateFields("),
    ),
    "static_src/js/yolomux/82_stats_current.js": (
        (
            "current plot-ready series owner",
            "function currentStatsGroups",
            "function currentStatsSeriesGroup",
            "function currentStatsChartHtml",
        ),
    ),
    # Finder, Tabber, and Differ deliberately have distinct product-specific callbacks, but their
    # selection, expansion, click, and keyboard mechanics must all flow through this one parent.
    # Keep this structural fact in the build lint rather than duplicating source-text regexes in
    # the node shard that exercises the controller's actual behavior.
    "static_src/js/yolomux/40_file_explorer_files.js": (
        (
            "shared tree controller parent",
            "function createSharedTreeInteractionController",
            "function sharedTreeSelectionApi",
            "function sharedTreeExpansionApi",
            "function sharedTreeClickHandler",
            "function sharedTreeKeyboardHandler",
            "const tabberTreeInteractionController = createSharedTreeInteractionController({",
        ),
    ),
    "static_src/js/yolomux/45_file_explorer_actions.js": (
        ("Finder tree controller registration", "const finderTreeInteractionController = createSharedTreeInteractionController({"),
    ),
    "static_src/js/yolomux/90_changes_editor.js": (
        ("Differ tree controller registration", "const differTreeInteractionController = createSharedTreeInteractionController({"),
    ),
    # Socket and queue state are one token-keyed record.  A second map lets reconnect, close, and
    # pruning disagree about ownership, which was the source of a prior share replay regression.
    "static_src/js/yolomux/00_bootstrap_state.js": (
        (
            "share sender lifecycle-record owner",
            "const shareSenderRecords = new Map()",
        ),
    ),
    "static_src/js/yolomux/96_share_state.js": (
        (
            "share host connection-record operations",
            "function shareHostConnectionRecord",
            "function enqueueShareHostMessage",
            "function sendOrQueueShareHostMessage",
            "function ensureShareHostSocket",
            "function ensureShareHostSockets",
        ),
    ),
}
SHARED_UI_OWNERSHIP_FORBIDDEN_NEEDLES = (
    ("parallel share host socket map", "shareHostSockets"),
    ("parallel share host queue map", "shareHostQueues"),
)
# Exact normalized production clones are rare; each reviewed exception must name why the two
# surfaces are deliberately separate.  The key is the stable sorted source-file pair plus the
# normalized block digest emitted by lint_normalized_production_clones().
NORMALIZED_PRODUCTION_CLONE_ALLOWLIST: dict[str, str] = {
    # ChatStore and LoginRateLimiter both use the standard double-checked lazy-init guard
    # (self._initialized flag + init lock + re-check) around a user_version WAL migration.
    # The mechanical prologue (open, WAL-enable, BEGIN IMMEDIATE, read user_version) is
    # already shared via atomic_file.open_wal_database/enable_wal_with_retry/begin_wal_migration;
    # what remains is the lazy-init skeleton wrapping two deliberately separate schemas (chat's
    # multi-step FTS ladder vs the throttle's single bucket table), each raising its own
    # migration-error type. Collapsing further would couple two unrelated schemas.
    "yolomux_lib/chat_store.py, yolomux_lib/login_rate_limit.py:2e5bfc1edef9": "shared lazy-init guard; schemas deliberately separate",
    "yolomux_lib/chat_store.py, yolomux_lib/login_rate_limit.py:e53fcf7e7da8": "shared lazy-init guard; schemas deliberately separate",
}
CSS_COLOR_LITERAL_PATTERN = r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]+\)"
CSS_COLOR_LITERAL_RE = re.compile(CSS_COLOR_LITERAL_PATTERN)
STANDARD_BORDER_RADIUS_TOKENS = {
    "3px": "--radius-sm",
    "4px": "--radius-control",
    "6px": "--radius-md",
    "8px": "--radius-lg",
    "999px": "--radius-pill",
}
STANDARD_COMPONENT_FONT_SIZE_TOKENS = {
    "10px": "--ui-font-size-2xs",
    "11px": "--ui-font-size-xs",
    "12px": "--ui-font-size-sm",
}
STANDARD_MOTION_DURATION_TOKENS = {
    "90ms": "--motion-interaction-fast",
    "100ms": "--motion-interaction-standard",
    "120ms": "--motion-disclosure",
    "700ms": "--motion-activity-duration",
    "900ms": "--motion-activity-duration",
    "0.7s": "--motion-activity-duration",
    "0.9s": "--motion-activity-duration",
}
STANDARD_SPACING_TOKENS = {
    f"{value}px": f"--space-{value}"
    for value in range(1, 13)
}
SHARED_CSS_DECLARATION_SETS = {
    "preformatted text wrapping": frozenset({
        ("white-space", "pre-wrap"),
        ("overflow-wrap", "anywhere"),
    }),
    "Info-tree field wrapping": frozenset({
        ("min-width", "0"),
        ("max-width", "100%"),
        ("overflow", "visible"),
        ("text-overflow", "clip"),
        ("white-space", "normal"),
        ("overflow-wrap", "anywhere"),
    }),
    "tmux window token alignment": frozenset({
        ("flex", "0 1 auto"),
        ("max-width", "100%"),
        ("margin-inline-start", "0"),
        ("justify-content", "flex-start"),
        ("overflow", "visible"),
        ("vertical-align", "middle"),
    }),
    "active-control paint": frozenset({
        ("color", "var(--active-control-text)"),
        ("background", "var(--active-control-bg)"),
        ("border-color", "var(--active-control-border)"),
    }),
    "disabled-control state": frozenset({
        ("opacity", "0.42"),
        ("cursor", "default"),
    }),
    "inactive-agent-marker paint": frozenset({
        ("color", "var(--agent-inactive-marker-text)"),
        ("background", "var(--agent-inactive-marker-bg)"),
        ("border-color", "var(--agent-inactive-marker-border)"),
    }),
    "pane pressed-control paint": frozenset({
        ("color", "var(--pane-ctl-pressed-fg, var(--pane-tab-active-text))"),
        ("background", "var(--pane-ctl-pressed-bg, var(--pane-tab-active-bg))"),
        ("border-color", "var(--pane-ctl-pressed-border, var(--pane-tab-active-border))"),
    }),
    "YO!info text-action reset": frozenset({
        ("padding", "0"),
        ("border", "0"),
        ("background", "transparent"),
        ("text-align", "left"),
        ("font", "inherit"),
        ("cursor", "pointer"),
    }),
    "agent identity-cluster layout": frozenset({
        ("display", "inline-flex"),
        ("align-items", "center"),
        ("gap", "var(--space-2)"),
        ("flex", "0 0 auto"),
        ("vertical-align", "middle"),
    }),
    "editor toolbar hover paint": frozenset({
        ("color", "var(--editor-toolbar-control-hover-fg)"),
        ("border-color", "var(--editor-toolbar-control-hover-border)"),
        ("background", "var(--editor-toolbar-control-hover-bg)"),
    }),
    "branch-indicator paint": frozenset({
        ("color", "var(--branch-indicator-text)"),
        ("background", "var(--branch-indicator-bg)"),
        ("border-color", "var(--branch-indicator-border)"),
    }),
    "server-update reload rest paint": frozenset({
        ("background", "var(--danger-strong)"),
        ("color", "var(--paint-white)"),
    }),
    "server-update reload hover paint": frozenset({
        ("border-color", "var(--danger-strong-border)"),
        ("background", "var(--danger-strong-hover)"),
        ("color", "var(--paint-white)"),
    }),
    "three-row grid scaffold": frozenset({
        ("display", "grid"),
        ("grid-template-rows", "var(--three-row-panel-layout)"),
    }),
    "close glyph stroke geometry": frozenset({
        ("content", '""'),
        ("position", "absolute"),
        ("top", "50%"),
        ("left", "50%"),
        ("width", "var(--panel-close-glyph-width)"),
        ("height", "var(--panel-close-glyph-height)"),
        ("border-radius", "var(--radius-pill)"),
        ("background", "currentColor"),
    }),
    "two-row debug-view layout": frozenset({
        ("display", "grid"),
        ("grid-template-rows", "auto minmax(0, 1fr)"),
        ("gap", "var(--space-8)"),
    }),
    "search-input focus ring": frozenset({
        ("outline", "0"),
        ("border-color", "var(--active-control-border)"),
        ("box-shadow", "var(--active-control-focus-shadow)"),
    }),
    "search-input shell": frozenset({
        ("height", "28px"),
        ("padding", "3px 8px"),
        ("color", "var(--text)"),
        ("background", "var(--panel)"),
        ("border", "1px solid var(--line)"),
        ("border-radius", "5px"),
        ("font", "var(--ui-font-size-sm)/1.2 var(--ui-font)"),
    }),
    "preview-surface shell": frozenset({
        ("z-index", "var(--z-file-editor-preview-layer)"),
        ("overflow", "auto"),
        ("padding", "12px 16px"),
        ("background", "var(--editor-preview-bg)"),
        ("color", "var(--text)"),
        ("font-family", "var(--ui-font)"),
        ("font-size", "var(--editor-preview-font-size)"),
        ("line-height", "1.35"),
    }),
    "circular chat-action geometry": frozenset({
        ("display", "inline-flex"),
        ("align-items", "center"),
        ("justify-content", "center"),
        ("width", "30px"),
        ("height", "30px"),
        ("flex", "0 0 auto"),
        ("padding", "0"),
        ("border-radius", "50%"),
        ("cursor", "pointer"),
    }),
    "file-panel title typography": frozenset({
        ("color", "var(--text)"),
        ("font-family", "var(--tab-font)"),
        ("font-size", "var(--ui-font-size)"),
        ("font-weight", "700"),
        ("white-space", "nowrap"),
    }),
    "changes trailing totals layout": frozenset({
        ("flex", "0 0 auto"),
        ("display", "inline-flex"),
        ("align-items", "baseline"),
        ("gap", "var(--space-8)"),
        ("margin-inline-start", "auto"),
        ("white-space", "nowrap"),
    }),
    "keyboard reference row layout": frozenset({
        ("display", "grid"),
        ("gap", "var(--space-6)"),
        ("align-items", "center"),
        ("padding", "2px 0"),
        ("border-bottom", "1px solid var(--paint-white-06)"),
        ("font", "var(--ui-font-size-sm)/1.25 var(--ui-font)"),
    }),
    "document preview body layout": frozenset({
        ("display", "flex"),
        ("flex-direction", "column"),
        ("padding", "0"),
        ("background", "var(--document-preview-bg)"),
    }),
    "document preview image fit": frozenset({
        ("display", "block"),
        ("max-width", "100%"),
        ("height", "auto"),
        ("object-fit", "contain"),
    }),
    "editor square toolbar control layout": frozenset({
        ("min-width", "20px"),
        ("width", "20px"),
        ("display", "inline-flex"),
        ("align-items", "center"),
        ("justify-content", "center"),
        ("padding", "0"),
        ("line-height", "1"),
    }),
    "tree expand-collapse fixed geometry": frozenset({
        ("box-sizing", "border-box"),
        ("width", "16px"),
        ("min-width", "16px"),
        ("max-width", "16px"),
        ("inline-size", "16px"),
        ("min-inline-size", "16px"),
        ("max-inline-size", "16px"),
        ("flex", "0 0 16px"),
        ("padding", "0"),
        ("font-size", "0"),
    }),
    "danger-status paint": frozenset({
        ("color", "var(--danger-text)"),
        ("background", "var(--danger-bg)"),
        ("border-color", "var(--danger-border)"),
    }),
    "compact agent SVG geometry": frozenset({
        ("flex-basis", "14px"),
        ("width", "14px"),
        ("height", "14px"),
    }),
    "light panel-surface paint": frozenset({
        ("color", "var(--text)"),
        ("background", "var(--panel)"),
        ("border-color", "var(--line)"),
    }),
    "single-line ellipsis": frozenset({
        ("min-width", "0"),
        ("overflow", "hidden"),
        ("text-overflow", "ellipsis"),
        ("white-space", "nowrap"),
    }),
    "vanilla-preview code surface": frozenset({
        ("color", "var(--markdown-html-light-text)"),
        ("background", "var(--lt-panel)"),
        ("border-color", "var(--lt-line)"),
    }),
    "inline-code paint": frozenset({
        ("color", "var(--code-inline)"),
        ("background", "var(--code-inline-bg)"),
        ("border", "1px solid var(--code-inline-border)"),
        ("border-radius", "var(--radius-sm)"),
    }),
    "light code-block paint": frozenset({
        ("color", "var(--lt-code-block-text)"),
        ("background", "var(--lt-code-block-bg)"),
        ("border-color", "var(--lt-code-block-border)"),
    }),
    "editor native selection paint": frozenset({
        ("color", "inherit !important"),
        ("background", "var(--editor-native-selection-bg) !important"),
    }),
    "vanilla nested-code reset": frozenset({
        ("color", "inherit !important"),
        ("background", "transparent !important"),
        ("border-color", "transparent"),
    }),
    "flexible tab text": frozenset({
        ("flex", "1 1 auto"),
        ("min-width", "0"),
        ("max-width", "none"),
    }),
    "shared link rest paint": frozenset({
        ("color", "var(--link-soft)"),
        ("text-decoration", "none"),
    }),
    "shared link hover paint": frozenset({
        ("color", "var(--link-soft-hover)"),
        ("text-decoration", "underline"),
    }),
    "path-drag outline": frozenset({
        ("outline", "2px dashed var(--pane-resizer-hover-bg)"),
        ("outline-offset", "-5px"),
    }),
    "file explorer chrome hover paint": frozenset({
        ("color", "var(--text)"),
        ("border-color", "var(--text)"),
    }),
    "YO!agent action hover paint": frozenset({
        ("border-color", "var(--active-control-focus-ring)"),
        ("color", "var(--text)"),
        ("outline", "0"),
    }),
    "topbar status surface shell": frozenset({
        ("flex", "0 0 auto"),
        ("display", "inline-flex"),
        ("align-items", "center"),
        ("height", "var(--compact-control-height)"),
        ("font-size", "var(--ui-font-size-2xs)"),
        ("cursor", "pointer"),
        ("white-space", "nowrap"),
    }),
    "compact overflow strip layout": frozenset({
        ("flex", "0 0 auto"),
        ("min-width", "0"),
        ("display", "inline-flex"),
        ("align-items", "center"),
        ("gap", "var(--space-1)"),
        ("overflow", "visible"),
    }),
    "centered 6px flex row": frozenset({
        ("display", "flex"),
        ("align-items", "center"),
        ("gap", "var(--space-6)"),
    }),
    "spaced 8px flex heading": frozenset({
        ("display", "flex"),
        ("align-items", "center"),
        ("justify-content", "space-between"),
        ("gap", "var(--space-8)"),
        ("min-width", "0"),
    }),
    "centered 6px inline row": frozenset({
        ("min-width", "0"),
        ("display", "inline-flex"),
        ("align-items", "center"),
        ("gap", "var(--space-6)"),
    }),
    "centered 5px inline control": frozenset({
        ("display", "inline-flex"),
        ("align-items", "center"),
        ("gap", "var(--space-5)"),
    }),
    "wrapping 6px action row": frozenset({
        ("display", "flex"),
        ("flex-wrap", "wrap"),
        ("gap", "var(--space-6)"),
    }),
    "trailing flex zone": frozenset({
        ("flex", "0 1 auto"),
        ("margin-inline-start", "auto"),
        ("justify-content", "flex-end"),
    }),
    "bordered control shell": frozenset({
        ("border", "1px solid var(--line)"),
        ("border-radius", "var(--radius-control)"),
        ("color", "var(--text)"),
    }),
    "Finder toolbar control shell": frozenset({
        ("height", "20px"),
        ("border", "1px solid var(--line)"),
        ("border-radius", "var(--radius-control)"),
        ("color", "var(--muted)"),
        ("background", "transparent"),
        ("font", "700 var(--ui-font-size-2xs)/1 var(--mono-font)"),
    }),
    "secondary surface paint": frozenset({
        ("border", "1px solid var(--line)"),
        ("background", "var(--panel2)"),
        ("color", "var(--text)"),
    }),
    "framed panel surface": frozenset({
        ("background", "var(--panel)"),
        ("border", "1px solid var(--line)"),
        ("border-radius", "var(--radius-md)"),
    }),
    "centered editor icon pseudo-element": frozenset({
        ("content", '""'),
        ("position", "absolute"),
        ("top", "50%"),
        ("left", "50%"),
        ("transform", "translate(-50%, -50%)"),
    }),
}
CODE_SYNTAX_COLOR_TOKENS = frozenset({
    "--code-atom",
    "--code-comment",
    "--code-control",
    "--code-function",
    "--code-invalid",
    "--code-keyword",
    "--code-number",
    "--code-property",
    "--code-string",
    "--code-tag",
    "--code-type",
    "--code-variable",
})
I18N_UNTRANSLATED_REPORT_SAMPLE_LIMIT = 10
I18N_ALLOWED_IDENTICAL_TERMS = {
    "aa", "ai", "apache", "api", "ci", "claude", "cli", "codex", "cpu", "css", "csv", "geojson", "git", "github", "gitlab", "head", "html", "http",
    "graph", "id", "ip", "javascript", "json", "jsonl", "linear", "linkedin", "markdown", "mermaid", "mit", "ndjson", "ok", "openai", "pdf",
    "polyform", "pr", "readme", "rss", "sse", "ssh", "tmux", "toml", "tsv", "url", "websocket", "worktree", "xml", "yaml", "yo!agent",
    "yo!info", "yo!share", "yo!stats", "yolo", "yolomux",
}
I18N_ALLOWED_IDENTICAL_KEYS = frozenset({
    "brand.marker",
    "brand.tab.agent",
    "brand.tab.changes",
    "brand.tab.info",
    "brand.tab.summary",
    "brand.wordmark.lo",
    "changes.titleForSession",
    "debug.graph.chart.cpu",
    "debug.graph.chart.gpuMemory",
    "debug.graph.chart.gpuUtil",
    "debug.graph.chart.memory",
    "debug.graph.control.charts",
    "debug.graph.meta.rss",
    "debug.graph.series.defaultProcessCpu",
    "debug.graph.series.processCpu",
    "debug.graph.series.systemMemory",
    "debug.system.localServices.exitedAgo",
    "debug.system.localServices.field.activeTask",
    "debug.system.localServices.field.clients",
    "debug.system.localServices.field.lastFailure",
    "debug.system.localServices.field.lastRan",
    "debug.system.localServices.field.memory",
    "debug.system.localServices.field.pid",
    "debug.system.localServices.field.queues",
    "debug.system.localServices.field.started",
    "debug.system.localServices.field.status",
    "debug.system.localServices.field.uptime",
    "debug.system.localServices.fieldColumn",
    "debug.system.localServices.prevPrefix",
    "debug.system.localServices.prevValue",
    "debug.system.localServices.serviceFallback",
    "debug.system.localServices.state.issue",
    "debug.system.localServices.state.running",
    "debug.system.localServices.title",
    "finder.label.finder",
    "notify.working.attention.body",
    "notify.working.attention.title",
    "notify.working.done.body",
    "notify.working.done.title",
    "pref.notifications.notify_working_attention.help",
    "pref.notifications.notify_working_attention.label",
    "pref.notifications.notify_working_done.help",
    "pref.notifications.notify_working_done.label",
    "shortcuts.keys.pinTab",
    "shortcuts.section.finderDiffer",
})
I18N_ALLOWED_IDENTICAL_LOCALE_KEYS: dict[str, frozenset[str]] = {
    "de": frozenset({"transcript.role.system"}),
    "fr": frozenset({"common.message"}),
    "it": frozenset({"menu.file"}),
    "pl": frozenset({"transcript.role.system"}),
}
I18N_PLACEHOLDER_PATTERN = r"\{[A-Za-z_][A-Za-z0-9_.-]*\}"
I18N_FORMAT_TOKEN_PATTERN = r"\{[A-Za-z_][A-Za-z0-9_.-]*:[^{}\s]+\}"
I18N_PLACEHOLDER_RE = re.compile(I18N_PLACEHOLDER_PATTERN)
I18N_PROTECTED_TOKEN_RE = re.compile(
    I18N_FORMAT_TOKEN_PATTERN
    + r"|`[^`\n]+`"
    r"|https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+"
    r"|/(?:static|assets?)/[A-Za-z0-9._~@+%/-]+"
    r"|</?[A-Za-z][^>]*>"
    r"|~/(?:[A-Za-z0-9_.{}@+-]+/)*[A-Za-z0-9_.{}@+-]*"
    r"|\b(?:Ctrl|Cmd)(?:[-+][A-Za-z0-9_.]+)+\b"
)
I18N_PSEUDO_TOKEN_RE = re.compile(f"({I18N_PLACEHOLDER_PATTERN}|{I18N_PROTECTED_TOKEN_RE.pattern})")
I18N_TRANSLATABLE_CODE_KEYS = frozenset({"yoagent.prompt.format"})
I18N_REQUIRED_YO_MARKERS = {"zh-Hans": "优", "zh-Hant": "優"}
I18N_ALLOWED_DUPLICATE_KEY_GROUPS: dict[frozenset[str], str] = {
    frozenset({"debug.summary", "searchHistory.summary", "transcript.role.summary"}): "view labels and a transcript role need different grammar",
    frozenset({"dialog.delete.kindFile", "popover.kind.file", "yolo.rule.source.file"}): "file kind, popover metadata, and rule source need different grammar",
    frozenset({"branch.current", "summary.state.active"}): "current branch and active work are different states",
    frozenset({"finder.dateMode.none", "info.group.none"}): "no date and no grouping are different choices",
    frozenset({"git.status.copied", "status.copied"}): "git copied status and clipboard completion are different events",
    frozenset({"legend.icon.share.label", "share.create"}): "share legend noun and create-share action are different parts of speech",
    frozenset({"menu.view.theme", "pref.appearance.editor_cursor_color.theme"}): "theme heading and inherit-theme cursor option are different concepts",
    frozenset({"pr.approved", "pr.review.approvedShort"}): "full review state and compact badge have different display constraints",
    frozenset({"state.blocked", "state.short.blocked"}): "full and compact blocked states have different display constraints",
    frozenset({"summary.state.idle", "yolo.status.idle"}): "summary state and inline worker status need different grammar",
    frozenset({"summary.stream.effortDefault", "yolo.rule.default"}): "effort default and rule name are different concepts",
    frozenset({"common.theme.system", "pref.general.language.system", "transcript.role.system"}): "theme, language, and transcript-role meanings of system are different concepts",
    frozenset({"tmuxWall.column.user", "transcript.role.user"}): "user column and transcript role need different grammar",
    frozenset({"toast.keep", "update.dismiss"}): "keep action and update dismissal are different actions",
    frozenset({"yoagent.stream.error", "yolo.rule.source.error"}): "stream state and rule source are different concepts",
}
I18N_ALLOWED_DUPLICATE_PLURAL_FAMILY_GROUPS: dict[frozenset[str], str] = {
    frozenset({"relative.compact.day", "summary.relative.day"}): "compact relative time and summary prose use different grammar",
}
I18N_LITERAL_CALL_RE = re.compile(
    r"\b(?P<function>t|localizedHtml|tPlural)\(\s*(?P<quote>['\"])(?P<key>[A-Za-z0-9_.-]+)(?P=quote)"
)
I18N_COMPLETE_LITERAL_KEY_RE = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")
I18N_VISIBLE_LITERAL_SINK_PATTERNS = (
    re.compile(r"\.(?:textContent|innerText|title|placeholder)\s*=\s*(?P<quote>['\"])(?P<value>[^'\"\n]+)(?P=quote)"),
    re.compile(r"\.setAttribute\(\s*['\"](?:aria-label|title|placeholder)['\"]\s*,\s*(?P<quote>['\"])(?P<value>[^'\"\n]+)(?P=quote)"),
    re.compile(r"\b(?:statusErr|statusOk|showToast|confirm|prompt)\(\s*(?P<quote>['\"])(?P<value>[^'\"\n]+)(?P=quote)"),
)
I18N_VISIBLE_TEMPLATE_SINK_PATTERNS = (
    re.compile(r"\.(?:textContent|innerText|title|placeholder)\s*=\s*`(?P<value>[^`\n]+)`"),
    re.compile(r"\.setAttribute\(\s*['\"](?:aria-label|title|placeholder)['\"]\s*,\s*`(?P<value>[^`\n]+)`"),
    re.compile(r"\b(?:statusErr|statusOk|showToast|confirm|prompt)\(\s*`(?P<value>[^`\n]+)`"),
)
I18N_VISIBLE_RAW_FIELD_RE = re.compile(r"\b(?P<value>(?:action\.label|checks?\.summary|payload\.error|data\.error))\b")
I18N_VISIBLE_SINK_RE = re.compile(
    r"\.(?:textContent|innerText|innerHTML|title|placeholder)\s*=|"
    r"\.setAttribute\(\s*['\"](?:aria-label|title|placeholder)['\"]|"
    r"\b(?:statusErr|statusOk|showToast|confirm|prompt)\("
)
I18N_CSS_CONTENT_RE = re.compile(r"\bcontent\s*:\s*(?P<quote>['\"])(?P<value>[^'\"\n]+)(?P=quote)")
I18N_VISIBLE_LITERAL_UNITS = frozenset({"b", "gb", "kb", "mb", "ms", "px", "s"})
_PSEUDO_ACCENTS = str.maketrans({
    "a": "á", "b": "ƀ", "c": "ç", "d": "đ", "e": "é", "f": "ƒ", "g": "ǧ", "h": "ĥ", "i": "í",
    "j": "ĵ", "k": "ķ", "l": "ł", "m": "ɱ", "n": "ñ", "o": "ó", "p": "ƥ", "q": "ɋ", "r": "ř",
    "s": "š", "t": "ť", "u": "ú", "v": "ṽ", "w": "ŵ", "x": "ẋ", "y": "ý", "z": "ž",
    "A": "Á", "B": "Ɓ", "C": "Ç", "D": "Đ", "E": "É", "F": "Ƒ", "G": "Ǧ", "H": "Ĥ", "I": "Í",
    "J": "Ĵ", "K": "Ķ", "L": "Ł", "M": "Ɱ", "N": "Ñ", "O": "Ó", "P": "Ƥ", "Q": "Ɋ", "R": "Ř",
    "S": "Š", "T": "Ť", "U": "Ú", "V": "Ṽ", "W": "Ŵ", "X": "Ẋ", "Y": "Ý", "Z": "Ž",
})

ASSETS: dict[str, list[str]] = {
    "emoji-data.js": [
        "static_src/js/emoji/emoji_data.js",
    ],
    "yolomux.js": [
        "static_src/js/yolomux/00_bootstrap_state.js",
        "static_src/js/yolomux/02_timing.js",
        "static_src/js/yolomux/05_i18n.js",
        "static_src/js/yolomux/10_core_utils.js",
        "static_src/js/yolomux/20_layout_state.js",
        "static_src/js/yolomux/30_app_menus.js",
        "static_src/js/yolomux/40_file_explorer_files.js",
        "static_src/js/yolomux/45_agent_window_activity.js",
        "static_src/js/yolomux/45_file_explorer_actions.js",
        "static_src/js/yolomux/46_file_drop_actions.js",
        "static_src/js/yolomux/50_editor_settings_runtime.js",
        "static_src/js/yolomux/60_popovers_tabs.js",
        "static_src/js/yolomux/70_layout_actions.js",
        "static_src/js/yolomux/75_dockview_layout.js",
        "static_src/js/yolomux/78_panel_dom_actions.js",
        "static_src/js/yolomux/78_panel_shell.js",
        "static_src/js/yolomux/79_conversation_shared.js",
        "static_src/js/yolomux/80_info_panel.js",
        "static_src/js/yolomux/81_yoagent_panel.js",
        "static_src/js/yolomux/82_chat_panel.js",
        "static_src/js/yolomux/82_preferences_panel.js",
        "static_src/js/yolomux/82_stats_current.js",
        "static_src/js/yolomux/83_stats_panel.js",
        "static_src/js/yolomux/83_debug_panel.js",
        "static_src/js/yolomux/90_changes_editor.js",
        "static_src/js/yolomux/92_editor_nav.js",
        "static_src/js/yolomux/93_markdown_preview.js",
        "static_src/js/yolomux/94_preview_renderers.js",
        "static_src/js/yolomux/96_pane_popout.js",
        "static_src/js/yolomux/94_preview_popout.js",
        "static_src/js/yolomux/95_codemirror_editor.js",
        "static_src/js/yolomux/96_share_state.js",
        "static_src/js/yolomux/97_share_replay.js",
        "static_src/js/yolomux/98_share_admin.js",
        "static_src/js/yolomux/99_terminal_boot.js",
    ],
    "yolomux.css": [
        "static_src/css/yolomux/00_tokens_base.css",
        "static_src/css/yolomux/10_topbar_menus.css",
        "static_src/css/yolomux/20_sessions_popovers.css",
        "static_src/css/yolomux/30_preferences_changes.css",
        "static_src/css/yolomux/40_layout_panes_tabs.css",
        "static_src/css/yolomux/50_terminal_file_tree.css",
        "static_src/css/yolomux/60_editor_file_panels.css",
    ],
}

# Source partials intentionally kept on disk but no longer concatenated into a served asset. A
# reviewed marker distinguishes retirement from the much more dangerous "forgot to register it"
# state that lint_asset_source_completeness() rejects.
RETIRED_ASSET_PARTS: dict[str, str] = {
    "static_src/js/yolomux/80_panes_preferences.js": (
        "superseded by 80_info_panel.js and 82_preferences_panel.js; retained during migration"
    ),
}


def repo_path(path: str | Path) -> Path:
    return REPO_ROOT / path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def generated_header(asset: str) -> str:
    return f"/* GENERATED by tools/static_build.py from static_src/ - DO NOT EDIT {asset}; edit the partials and rebuild. */\n"


def build_asset(asset: str) -> str:
    parts = ASSETS[asset]
    return generated_header(asset) + "".join(read_text(repo_path(part)) for part in parts)


def write_asset(asset: str) -> bool:
    output_path = repo_path("static") / asset
    next_text = build_asset(asset)
    try:
        current = read_text(output_path)
    except FileNotFoundError:
        current = None
    if current == next_text:
        return False
    output_path.write_text(next_text, encoding="utf-8")
    return True


def check_asset(asset: str) -> bool:
    output_path = repo_path("static") / asset
    return read_text(output_path) == build_asset(asset)


def check_css_braces() -> None:
    """Fail the build if any CSS partial has unbalanced { } braces.

    A truncated/incomplete rule (open brace, no close) is invisible in isolation but, once the partials
    are concatenated, silently swallows the start of the NEXT partial's CSS until a stray } rebalances
    it (this exact bug shipped once — B1). Strips /* */ comments and quoted strings first so
    braces inside content/url() values don't false-positive.
    """
    for asset, parts in ASSETS.items():
        if not asset.endswith(".css"):
            continue
        for part in parts:
            text = read_text(repo_path(part))
            stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            stripped = re.sub(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'", "", stripped)
            opens, closes = stripped.count("{"), stripped.count("}")
            if opens != closes:
                raise BuildError(
                    f"{part}: unbalanced CSS braces ({opens} '{{' vs {closes} '}}') — a truncated or "
                    f"incomplete rule? An open rule swallows the next partial's CSS in the bundle."
                )


# Option-2 / "Durable Fix B": flag a DARK-default rule that hardcodes a theme-extreme literal color on a
# theme-sensitive property with NO body.theme-light counterpart — the root cause of every recurring
# light-mode dark-box / invisible-text bug (/19/25/28). Precision-first: only near-opaque,
# near-extreme literals (very dark backgrounds / very light text) are flagged, so vibrant brand/status
# colors that read in both themes don't false-positive. Selectors that are intentionally theme-
# independent live in LIGHT_LINT_ALLOWLIST (vetted, with a reason).
_THEME_LIGHT_SELECTORS = ("body.theme-light", "body.editor-theme-light")
_THEME_PROPS = ("background", "background-color", "color")

# Vetted intentional exceptions: a selector whose extreme literal is deliberate in BOTH themes (dark
# popovers/dialogs, terminal surfaces, the dark editor-theme swatch, fixed state badges, white-on-accent
# hovers). Reviewed 2026-06-03; AUDIT-LIGHTMODE left these dark-by-design. Anything NOT here is reported.
LIGHT_LINT_ALLOWLIST: dict[str, str] = {
    # Dark popovers / dialogs / overlays / drag ghost — intentionally dark in both themes.
    ".file-drag-image.drag-image": "drag-ghost overlay, dark by design",
    ".file-drag-icon": "drag-ghost overlay",
    ".file-drag-title": "drag-ghost overlay",
    ".terminal-context-menu": "dark popover by design (AUDIT-LIGHTMODE)",
    ".terminal-context-menu-separator": "dark popover divider",
    ".session-rename-dialog": "dark popover by design",
    ".session-rename-input": "dark popover input by design",
    ".session-rename-actions button": "dark popover button by design",
    ".file-image-preview-popover": "dark hover popover by design",
    ".modal": "dark modal by design",
    ".file-editor-dialog": "dark modal by design (AUDIT-LIGHTMODE)",
    ".file-editor-dialog-action": "dark modal button by design",
    # Terminal surfaces follow the TERMINAL theme, not the app theme.
    ".terminal-error": "terminal surface, follows the terminal theme",
    ".tmux-snapshot": "terminal snapshot surface",
    # Fixed state-badge / marker / warning colors — theme-independent (light text sits on the colored
    # badge in both themes; red attention banners are red in both).
    ".session-state-disconnected": "fixed state-badge color",
    ".session-state-needs-approval": "fixed red attention-badge color",
    ".session-state-needs-input": "fixed red attention-badge color",
    ".session-state-blocked": "fixed red attention-badge color",
    ".session-yolo-marker.locked": "fixed marker color",
    ".transport-warning": "fixed red warning-banner color",
    # White text on the accent (green) hover/focus background — reads in both themes.
    ".info-sort-button:hover": "white text on the accent hover bg",
    ".info-sort-button:focus-visible": "white text on the accent focus bg",
    ".file-explorer-close:hover": "white text on accent hover",
    ".file-explorer-close:focus-visible": "white text on accent focus",
    ".file-editor-close:hover": "white text on accent hover",
    ".file-editor-close:focus-visible": "white text on accent focus",
    ".file-explorer-panel-close:hover": "white text on accent hover",
    ".file-explorer-panel-close:focus-visible": "white text on accent focus",
    ".file-editor-panel-close:hover": "white text on accent hover",
    ".file-editor-panel-close:focus-visible": "white text on accent focus",
    # The DARK editor-theme swatch/panel is intentionally dark regardless of the app theme.
    ".file-editor-theme.theme-dark": "dark editor-theme swatch (intentional in both app themes)",
    ".file-editor-theme-panel.theme-dark": "dark editor-theme swatch",
    # Diff/conflict code surfaces are dark code blocks by design.
    ".file-editor-conflict-compare pre": "dark code/diff block by design",
    ".file-compare-line.added": "diff added-line text on the dark diff surface",
    # NOTE: .session-button-name / .session-button-dir / .markdown-body pre USED to need allowlisting
    # (their light overrides are on more-specific contextual selectors); the contextual pairing in
    # _light_covers() now recognizes those, so they no longer need entries here.
}


def _iter_css_rules(css: str):
    """Yield (selector, declaration-body) for every style rule; descend @media/@supports; skip the
    bodies of @keyframes/@font-face/@page (their inner blocks are not theme-sensitive style rules)."""
    i, n = 0, len(css)
    buf: list[str] = []
    while i < n:
        char = css[i]
        if char == "{":
            selector = "".join(buf).strip()
            buf = []
            depth, j = 1, i + 1
            while j < n and depth:
                if css[j] == "{":
                    depth += 1
                elif css[j] == "}":
                    depth -= 1
                j += 1
            body = css[i + 1:j - 1]
            if selector.startswith("@"):
                if selector.split()[0].lower() in ("@media", "@supports"):
                    yield from _iter_css_rules(body)
            else:
                yield selector, body
            i = j
            continue
        buf.append(char)
        i += 1


def _css_without_comments(css: str) -> str:
    """Remove comments without changing line numbers used by structural lint diagnostics."""
    return re.sub(
        r"/\*.*?\*/",
        lambda match: "\n" * match.group(0).count("\n"),
        css,
        flags=re.DOTALL,
    )


def _css_without_functions(css: str, function_names: set[str]) -> str:
    """Mask selected CSS functions while preserving line numbers and unrelated literals."""
    masked = list(css)
    offset = 0
    function_re = re.compile(rf"\b(?:{'|'.join(re.escape(name) for name in sorted(function_names))})\s*\(")
    while match := function_re.search(css, offset):
        start = match.start()
        index = match.end()
        depth = 1
        quote = ""
        while index < len(css) and depth:
            char = css[index]
            if quote:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = ""
            elif char in {'"', "'"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        index = min(index, len(css))
        for position in range(start, index):
            if masked[position] != "\n":
                masked[position] = " "
        offset = index
    return "".join(masked)


def _css_without_var_functions(css: str) -> str:
    """Mask var(...) calls while preserving line numbers and every unrelated declaration literal."""
    return _css_without_functions(css, {"var"})


def _css_without_custom_property_values(css: str) -> str:
    """Mask local `--token: value` bodies while preserving their declarations and line numbers."""
    declaration_re = re.compile(
        r"(?P<head>(?:^|[;{])\s*--[\w-]+\s*:)(?P<value>[^;{}]*)",
        re.MULTILINE,
    )

    def masked(match: re.Match[str]) -> str:
        value = "".join("\n" if char == "\n" else " " for char in match.group("value"))
        return match.group("head") + value

    return declaration_re.sub(masked, css)


def _iter_located_css_rules(
    css: str,
    *,
    context: tuple[str, ...] = (),
    base_line: int = 1,
):
    """Yield (context, selector, body, opening-brace line) for style rules.

    Duplicate selectors are meaningful only inside the same at-rule context. Descend through the
    conditional grouping rules used by this stylesheet, while treating keyframes/font-face/page as
    non-style-rule bodies. The scanner skips quoted braces so generated content cannot split a rule.
    """
    i = 0
    head_start = 0
    length = len(css)
    nested_at_rules = {"@container", "@layer", "@media", "@supports"}
    while i < length:
        char = css[i]
        if char in {'"', "'"}:
            quote = char
            i += 1
            while i < length:
                if css[i] == "\\":
                    i += 2
                    continue
                if css[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if char == ";":
            head_start = i + 1
            i += 1
            continue
        if char != "{":
            i += 1
            continue
        header = css[head_start:i].strip()
        opening_line = base_line + css.count("\n", 0, i)
        depth = 1
        j = i + 1
        quote = ""
        while j < length and depth:
            current = css[j]
            if quote:
                if current == "\\":
                    j += 2
                    continue
                if current == quote:
                    quote = ""
            elif current in {'"', "'"}:
                quote = current
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
            j += 1
        body = css[i + 1:j - 1]
        if header.startswith("@"):
            keyword = header.split(None, 1)[0].lower()
            if keyword in nested_at_rules:
                normalized_header = " ".join(header.split())
                yield from _iter_located_css_rules(
                    body,
                    context=(*context, normalized_header),
                    base_line=opening_line,
                )
        elif header:
            yield context, " ".join(header.split()), body, opening_line
        i = j
        head_start = i


def _split_css_selector_list(selector: str) -> list[str]:
    """Split one selector list without treating commas inside :is(), :not(), or attributes as separators."""
    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    for index, char in enumerate(selector):
        if quote:
            if char == quote and (index == 0 or selector[index - 1] != "\\"):
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(selector[start:index].strip())
            start = index + 1
    parts.append(selector[start:].strip())
    return [part for part in parts if part]


def lint_css_structure() -> list[str]:
    """Reject CSS cascade ownership that would otherwise depend on source order.

    A property repeated in one rule silently discards its first value, the same selector repeated in
    one context splits ownership across arbitrary locations, an empty rule is dead migration debris,
    and orphan text after the final rule is silently discarded by browsers. Conditional copies in
    different @media/@supports contexts remain valid.
    """
    errors: list[str] = []
    selector_locations: dict[tuple[tuple[str, ...], str], list[str]] = defaultdict(list)
    declaration_re = re.compile(r"(?:^|;)\s*(--[\w-]+|[\w-]+)\s*:")
    for part in ASSETS.get("yolomux.css", []):
        path = repo_path(part)
        try:
            css = _css_without_comments(read_text(path))
        except FileNotFoundError:
            continue
        last_rule_end = css.rfind("}")
        trailing = css[last_rule_end + 1:] if last_rule_end >= 0 else ""
        orphan = re.search(r"\S+", trailing)
        if orphan:
            orphan_index = last_rule_end + 1 + orphan.start()
            orphan_line = 1 + css.count("\n", 0, orphan_index)
            orphan_text = " ".join(trailing[orphan.start():].split())
            if len(orphan_text) > 60:
                orphan_text = f"{orphan_text[:57]}..."
            errors.append(f"{part}:{orphan_line}: orphan CSS text after final rule: {orphan_text!r}")
        for context, selector, body, line_no in _iter_located_css_rules(css):
            location = f"{part}:{line_no}"
            declarations = declaration_re.findall(body)
            if not declarations:
                errors.append(f"{location}: empty CSS rule '{selector}'")
            duplicate_properties = sorted({name for name in declarations if declarations.count(name) > 1})
            for name in duplicate_properties:
                errors.append(f"{location}: CSS rule '{selector}' declares '{name}' more than once")
            selector_locations[(context, selector)].append(location)
    for (context, selector), locations in sorted(selector_locations.items()):
        if len(locations) < 2:
            continue
        context_text = " > ".join(context) if context else "top level"
        errors.append(
            f"duplicate CSS selector '{selector}' in {context_text}: {', '.join(locations)}; merge it into one owner"
        )
    return errors


def _theme_base_selector(selector: str) -> str | None:
    for prefix in (
        "body.theme-light",
        "body.editor-theme-light",
        ":where(body.theme-light)",
        ":where(body.editor-theme-light)",
    ):
        if selector.startswith(prefix + " "):
            return selector[len(prefix):].strip()
    return None


def lint_identical_theme_restatements() -> list[str]:
    """Reject light-theme declarations that repeat the same value already owned by the base selector."""
    declaration_re = re.compile(r"(?:^|;)\s*(--[\w-]+|[\w-]+)\s*:\s*([^;}]+)", re.IGNORECASE)
    base_declarations: dict[tuple[tuple[str, ...], str, str], list[tuple[str, str]]] = defaultdict(list)
    light_rules: list[tuple[tuple[str, ...], str, str, dict[str, str], str]] = []
    for part in ASSETS.get("yolomux.css", []):
        path = repo_path(part)
        try:
            css = _css_without_comments(read_text(path))
        except FileNotFoundError:
            continue
        for context, selector_list, body, line_no in _iter_located_css_rules(css):
            declarations = {
                match.group(1).lower(): " ".join(match.group(2).split())
                for match in declaration_re.finditer(body)
            }
            location = f"{part}:{line_no}"
            for selector in _split_css_selector_list(selector_list):
                base_selector = _theme_base_selector(selector)
                if base_selector is not None:
                    light_rules.append((context, selector, base_selector, declarations, location))
                    continue
                for name, value in declarations.items():
                    base_declarations[(context, selector, name)].append((value, location))
    errors: list[str] = []
    for context, selector, base_selector, declarations, location in light_rules:
        for name, value in declarations.items():
            owners = [
                owner_location
                for owner_value, owner_location in base_declarations.get((context, base_selector, name), [])
                if owner_value == value
            ]
            if owners:
                errors.append(
                    f"{location}: theme selector '{selector}' restates '{name}: {value}' from "
                    f"{', '.join(owners)} base selector '{base_selector}'; remove the copy or lower fallback specificity"
                )
    return sorted(errors)


def lint_repeated_semantic_declaration_sets() -> list[str]:
    """Semantic paint sets belong to one grouped selector, independent of declaration order."""
    declaration_re = re.compile(r"(?:^|;)\s*([\w-]+)\s*:\s*([^;}]+)", re.IGNORECASE)
    occurrences: dict[str, list[str]] = defaultdict(list)
    for part in ASSETS.get("yolomux.css", []):
        path = repo_path(part)
        try:
            css = _css_without_comments(read_text(path))
        except FileNotFoundError:
            continue
        for _context, _selector, body, rule_line in _iter_located_css_rules(css):
            declarations = frozenset(
                (match.group(1).lower(), " ".join(match.group(2).split()))
                for match in declaration_re.finditer(body)
            )
            for name, required in SHARED_CSS_DECLARATION_SETS.items():
                if required <= declarations:
                    occurrences[name].append(f"{part}:{rule_line}")
    return [
        f"semantic CSS declaration set {name!r} repeats in {', '.join(locations)}; merge it into one grouped selector"
        for name, locations in sorted(occurrences.items())
        if len(locations) > 1
    ]


def lint_code_syntax_color_ownership() -> list[str]:
    """Each semantic syntax color has one rule shared by every renderer."""
    color_re = re.compile(r"(?:^|;)\s*color\s*:\s*var\((--code-[\w-]+)\)\s*!important", re.IGNORECASE)
    occurrences: dict[str, list[str]] = defaultdict(list)
    for part in ASSETS.get("yolomux.css", []):
        path = repo_path(part)
        try:
            css = _css_without_comments(read_text(path))
        except FileNotFoundError:
            continue
        for _context, selector, body, rule_line in _iter_located_css_rules(css):
            if not re.search(r"\.(?:hljs|code)-", selector):
                continue
            for match in color_re.finditer(body):
                token = match.group(1).lower()
                if token in CODE_SYNTAX_COLOR_TOKENS:
                    occurrences[token].append(f"{part}:{rule_line}")
    errors = []
    for token in sorted(CODE_SYNTAX_COLOR_TOKENS):
        locations = occurrences.get(token, [])
        if not locations:
            errors.append(f"syntax color {token!r} has no shared .hljs-/.code- owner")
        elif len(locations) > 1:
            errors.append(f"syntax color {token!r} repeats in {', '.join(locations)}; merge renderer selectors into one rule")
    return errors


def lint_raw_standard_border_radii() -> list[str]:
    """Standard component corners must reference the existing radius design tokens.

    The token partial is the only literal owner. Component copies make a global radius change require
    edits across every CSS partial and let otherwise-identical controls drift by a pixel.
    """
    errors: list[str] = []
    property_re = re.compile(r"\bborder(?:-[a-z-]+)?-radius\s*:\s*([^;}]+)")
    literals = "|".join(re.escape(value) for value in STANDARD_BORDER_RADIUS_TOKENS)
    literal_re = re.compile(rf"(?<![\w.])({literals})(?![\w-])")
    for part in ASSETS.get("yolomux.css", []):
        if part.endswith("00_tokens_base.css"):
            continue
        path = repo_path(part)
        try:
            css = _css_without_comments(read_text(path))
        except FileNotFoundError:
            continue
        for declaration in property_re.finditer(css):
            line_no = 1 + css.count("\n", 0, declaration.start())
            raw_values = dict.fromkeys(literal_re.findall(declaration.group(1)))
            for value in raw_values:
                token = STANDARD_BORDER_RADIUS_TOKENS[value]
                errors.append(f"{part}:{line_no}: raw standard border radius {value}; use var({token})")
    return errors


def lint_raw_standard_motion_durations() -> list[str]:
    """Shared UI cadence literals belong to the motion tokens in the base partial."""
    errors: list[str] = []
    literal_pattern = "|".join(
        re.escape(value) for value in sorted(STANDARD_MOTION_DURATION_TOKENS, key=len, reverse=True)
    )
    literal_re = re.compile(rf"(?<![\w.])({literal_pattern})(?![\w.])", re.IGNORECASE)
    for part in ASSETS.get("yolomux.css", []):
        if part.endswith("00_tokens_base.css"):
            continue
        path = repo_path(part)
        try:
            css = _css_without_var_functions(_css_without_comments(read_text(path)))
        except FileNotFoundError:
            continue
        seen: set[tuple[int, str]] = set()
        for match in literal_re.finditer(css):
            value = match.group(1).lower()
            line_no = 1 + css.count("\n", 0, match.start())
            if (line_no, value) in seen:
                continue
            seen.add((line_no, value))
            token = STANDARD_MOTION_DURATION_TOKENS[value]
            errors.append(f"{part}:{line_no}: raw standard motion duration {value}; use var({token})")
    return errors


def lint_raw_standard_font_sizes() -> list[str]:
    """Standard component text sizes must follow the responsive UI type scale."""
    errors: list[str] = []
    property_re = re.compile(r"\b(font-size|font)\s*:\s*([^;}]+)", re.IGNORECASE)
    literal_pattern = "|".join(re.escape(value) for value in STANDARD_COMPONENT_FONT_SIZE_TOKENS)
    shorthand_re = re.compile(rf"(?<![/\w.-])({literal_pattern})(?=\s*(?:/|\s|$))", re.IGNORECASE)
    for part in ASSETS.get("yolomux.css", []):
        if part.endswith("00_tokens_base.css"):
            continue
        path = repo_path(part)
        try:
            css = _css_without_comments(read_text(path))
        except FileNotFoundError:
            continue
        for declaration in property_re.finditer(css):
            property_name = declaration.group(1).lower()
            raw_value = re.sub(r"\s*!important\s*$", "", declaration.group(2), flags=re.IGNORECASE).strip()
            if property_name == "font-size":
                value = raw_value.lower() if raw_value.lower() in STANDARD_COMPONENT_FONT_SIZE_TOKENS else ""
            else:
                masked = _css_without_functions(raw_value, {"var", "calc", "min", "max", "clamp"})
                match = shorthand_re.search(masked)
                value = match.group(1).lower() if match else ""
            if not value:
                continue
            line_no = 1 + css.count("\n", 0, declaration.start())
            token = STANDARD_COMPONENT_FONT_SIZE_TOKENS[value]
            errors.append(f"{part}:{line_no}: raw standard component font size {value}; use var({token})")
    return errors


def lint_raw_standard_spacing() -> list[str]:
    """Standard component gaps, padding, and margins must reference the shared spacing scale."""
    errors: list[str] = []
    property_re = re.compile(
        r"(?:^|;)\s*(gap|row-gap|column-gap|padding(?:-[a-z-]+)?|margin(?:-[a-z-]+)?)\s*:\s*([^;}]+)",
        re.IGNORECASE | re.MULTILINE,
    )
    literals = "|".join(re.escape(value) for value in sorted(STANDARD_SPACING_TOKENS, key=len, reverse=True))
    literal_re = re.compile(rf"(?<![\w.])({literals})(?![\w-])", re.IGNORECASE)
    invalid_negated_token_re = re.compile(r"(?<![\w-])-var\((--space-(?:[1-9]|1[0-2]))\)", re.IGNORECASE)
    for part in ASSETS.get("yolomux.css", []):
        if part.endswith("00_tokens_base.css"):
            continue
        path = repo_path(part)
        try:
            css = _css_without_comments(read_text(path))
        except FileNotFoundError:
            continue
        for _context, _selector, body, rule_line in _iter_located_css_rules(css):
            for declaration in property_re.finditer(body):
                line_no = rule_line + body.count("\n", 0, declaration.start(1))
                for token in dict.fromkeys(invalid_negated_token_re.findall(declaration.group(2))):
                    errors.append(
                        f"{part}:{line_no}: invalid negated spacing token -var({token}); use calc(-1 * var({token}))"
                    )
                for value in dict.fromkeys(match.lower() for match in literal_re.findall(declaration.group(2))):
                    errors.append(f"{part}:{line_no}: raw standard spacing {value}; use var({STANDARD_SPACING_TOKENS[value]})")
    return errors


def _color_rgba(color: str) -> tuple[tuple[int, int, int], float] | None:
    """Return normalized integer RGB channels and alpha for a literal CSS color."""
    color = color.strip().lower()
    rgb: list[int] = []
    alpha = 1.0
    hex_match = re.fullmatch(r"#([0-9a-f]{3,8})", color)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) in (3, 4):
            rgb = [int(digits[k] * 2, 16) for k in range(3)]
            if len(digits) == 4:
                alpha = int(digits[3] * 2, 16) / 255
        elif len(digits) in (6, 8):
            rgb = [int(digits[k:k + 2], 16) for k in (0, 2, 4)]
            if len(digits) == 8:
                alpha = int(digits[6:8], 16) / 255
        else:
            return None
    else:
        rgb_match = re.fullmatch(r"rgba?\(([^)]+)\)", color)
        if not rgb_match:
            return None
        parts = [p for p in re.split(r"[,\s/]+", rgb_match.group(1)) if p]
        if len(parts) < 3:
            return None
        try:
            for part in parts[:3]:
                rgb.append(int(round(float(part.rstrip("%")) * (2.55 if part.endswith("%") else 1))))
            if len(parts) >= 4:
                alpha = float(parts[3].rstrip("%")) / (100 if parts[3].endswith("%") else 1)
        except ValueError:
            return None

    channels = tuple(max(0, min(255, value)) for value in rgb[:3])
    return channels, max(0.0, min(1.0, alpha))


def _canonical_opaque_color(color: str) -> str | None:
    measured = _color_rgba(color)
    if measured is None:
        return None
    channels, alpha = measured
    if not math.isclose(alpha, 1.0):
        return None
    return "#" + "".join(f"{value:02x}" for value in channels)


def _color_identity(color: str) -> tuple[int, int, int, float] | None:
    measured = _color_rgba(color)
    if measured is None:
        return None
    channels, alpha = measured
    return (*channels, round(alpha, 6))


def _color_identity_label(identity: tuple[int, int, int, float]) -> str:
    red, green, blue, alpha = identity
    if math.isclose(alpha, 1.0):
        return f"#{red:02x}{green:02x}{blue:02x}"
    return f"rgb({red} {green} {blue} / {alpha:g})"


def _color_luminance_alpha(color: str) -> tuple[float, float] | None:
    """(relative luminance 0..1, alpha 0..1) for a #hex / rgb()/rgba() literal, else None."""
    measured = _color_rgba(color)
    if measured is None:
        return None
    rgb, alpha = measured

    def linear(value: int) -> float:
        v = max(0, min(255, value)) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * linear(rgb[0]) + 0.7152 * linear(rgb[1]) + 0.0722 * linear(rgb[2])
    return lum, alpha


def _first_color_literal(value: str) -> str | None:
    """The first opaque-ish literal color in a declaration value, or None if it is themed/adaptive
    (var(), color-mix(), a gradient) or has no literal color."""
    low = value.lower()
    if "var(" in low or "color-mix(" in low or "gradient(" in low:
        return None
    hex_match = re.search(r"#[0-9a-fA-F]{3,8}\b", value)
    if hex_match:
        return hex_match.group(0)
    rgb_match = re.search(r"rgba?\([^)]+\)", value)
    if rgb_match:
        return rgb_match.group(0)
    return None


def _prop_key(prop: str) -> str:
    return "background" if prop in ("background", "background-color") else prop


def _light_covers(dark_sel: str, light_bases: list[str]) -> bool:
    """Contextual (specificity-aware) pairing: a dark rule for selector S is covered when a
    body.theme-light rule targets S itself OR S in a MORE-SPECIFIC context — i.e. some light base
    selector equals S or ends with " <S>" (a descendant suffix). Catches the common real pattern where
    the light override is scoped (e.g. dark `.session-button-name` covered by
    `body.theme-light .pane-tab:not(.active) .session-button-name`), which exact-selector pairing missed."""
    for base in light_bases:
        if base == dark_sel or base.endswith(" " + dark_sel):
            return True
    return False


def lint_light_mode_pairs() -> list[str]:
    css = "\n".join(read_text(repo_path(part)) for part in ASSETS["yolomux.css"])
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    light_by_prop: dict[str, list[str]] = {}
    dark: list[tuple[str, str, str]] = []
    for selector, body in _iter_css_rules(css):
        decls = []
        for chunk in body.split(";"):
            if ":" not in chunk:
                continue
            name, _, val = chunk.partition(":")
            name, val = name.strip().lower(), val.strip()
            if name in _THEME_PROPS:
                decls.append((name, val))
        if not decls:
            continue
        for sel in (s.strip() for s in selector.split(",") if s.strip()):
            is_light = any(token in sel for token in _THEME_LIGHT_SELECTORS)
            base = sel
            for token in _THEME_LIGHT_SELECTORS:
                base = base.replace(token + " ", "").replace(token, "").strip()
            for name, val in decls:
                if is_light:
                    light_by_prop.setdefault(_prop_key(name), []).append(base)
                else:
                    dark.append((sel, name, val))
    violations: list[str] = []
    for sel, name, val in dark:
        if sel in LIGHT_LINT_ALLOWLIST:
            continue
        color = _first_color_literal(val)
        if not color:
            continue
        measured = _color_luminance_alpha(color)
        if not measured:
            continue
        lum, alpha = measured
        if alpha < 0.6:   # translucent overlays adapt to whatever shows through; not a dark box
            continue
        is_bg = name in ("background", "background-color")
        extreme = (is_bg and lum < 0.18) or (name == "color" and lum > 0.82)
        if not extreme:
            continue
        if _light_covers(sel, light_by_prop.get(_prop_key(name), [])):
            continue   # has a body.theme-light override (exact or in a more-specific context)
        kind = "dark background" if is_bg else "near-white text"
        violations.append(f"{sel} {{ {name}: {color} }} — {kind} with no body.theme-light override")
    return violations


class BuildError(Exception):
    """Raised when the build cannot proceed (e.g. i18n key-parity failure)."""


def source_catalogs() -> dict[str, dict]:
    """All hand-authored locale catalogs from static_src/locales (excludes the generated pseudo)."""
    catalogs: dict[str, dict] = {}
    if not LOCALES_SRC.is_dir():
        return catalogs
    for path in sorted(LOCALES_SRC.glob("*.json")):
        locale = path.stem
        if locale == PSEUDO_LOCALE:
            continue
        catalogs[locale] = json.loads(read_text(path))
    return catalogs


def plural_family_bases(source: dict[str, object]) -> set[str]:
    """Catalog bases whose source locale defines the required one/other fallback pair."""
    return {
        key[:-4]
        for key in source
        if key.endswith(".one") and f"{key[:-4]}.other" in source
    }


def locale_expected_keys(source: dict[str, object], locale: str) -> set[str]:
    """Ordinary source keys plus exactly the CLDR forms required by one shipped locale."""
    expected = set(source)
    categories = PLURAL_CATEGORIES_BY_LOCALE.get(locale, PLURAL_CATEGORIES_BY_LOCALE[SOURCE_LOCALE])
    for base in plural_family_bases(source):
        expected.update(f"{base}.{category}" for category in categories)
    return expected


def locale_source_key(source: dict[str, object], key: str) -> str:
    """Return the source-locale key whose token contract a locale-only plural form inherits."""
    if key in source:
        return key
    base, _separator, _category = key.rpartition(".")
    other = f"{base}.other"
    return other if other in source else key


def locale_registry_errors(catalogs: dict[str, dict]) -> list[str]:
    """Source catalog stems must exactly match the canonical shipped-locale registry."""
    errors: list[str] = []
    registered = set(SHIPPED_LOCALES)
    discovered = set(catalogs)
    missing_catalogs = sorted(registered - discovered)
    extra_catalogs = sorted(discovered - registered)
    if missing_catalogs:
        errors.append(f"missing registered locale catalogs: {', '.join(missing_catalogs)}")
    if extra_catalogs:
        errors.append(f"unregistered source locale catalogs: {', '.join(extra_catalogs)}")
    return errors


def locale_key_errors(catalogs: dict[str, dict]) -> list[str]:
    """Every catalog must have exactly its source keys plus locale-required plural forms."""
    errors: list[str] = []
    source = catalogs.get(SOURCE_LOCALE)
    if source is None:
        return errors
    for locale, catalog in catalogs.items():
        if locale == SOURCE_LOCALE:
            continue
        source_keys = locale_expected_keys(source, locale)
        keys = set(catalog)
        missing = sorted(source_keys - keys)
        extra = sorted(keys - source_keys)
        if missing:
            errors.append(f"{locale}.json missing keys: {', '.join(missing)}")
        if extra:
            errors.append(f"{locale}.json has unknown keys: {', '.join(extra)}")
    return errors


PYTHON_I18N_KEY_ARGUMENTS = {
    "RequestValidationError": 1,
    "message_descriptor": 0,
    "message_fields": 1,
    "rule_name_fields": 1,
    "server_string": 1,
    "update_last_action": 0,
    "user_message_payload": 0,
    "yoagent_text": 1,
}
PYTHON_I18N_PLURAL_KEY_ARGUMENTS = {
    "server_plural": 1,
}


def i18n_literal_key_errors(source: dict[str, object], paths: list[Path] | None = None) -> list[str]:
    """Every complete literal runtime key must resolve in the English catalog."""
    errors: list[str] = []
    source_paths = paths if paths is not None else [
        *(REPO_ROOT / path for path in ASSETS["yolomux.js"]),
        *sorted((REPO_ROOT / "yolomux_lib").rglob("*.py")),
    ]
    for path in source_paths:
        text = read_text(path)
        display_path = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path.name
        if path.suffix == ".py":
            calls: list[tuple[int, str, bool]] = []
            tree = ast.parse(text, filename=str(path))
            for node in ast.walk(tree):
                target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
                value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
                if isinstance(target, ast.Name) and target.id.endswith("_I18N_KEY_MAP") and isinstance(value, ast.Dict):
                    calls.extend(
                        (item.lineno, item.value, False)
                        for item in value.values
                        if isinstance(item, ast.Constant) and isinstance(item.value, str)
                    )
                if not isinstance(node, ast.Call):
                    continue
                function_name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                plural = function_name in PYTHON_I18N_PLURAL_KEY_ARGUMENTS
                key_index = (
                    PYTHON_I18N_PLURAL_KEY_ARGUMENTS.get(function_name)
                    if plural
                    else PYTHON_I18N_KEY_ARGUMENTS.get(function_name)
                )
                key_nodes = []
                if key_index is not None and len(node.args) > key_index:
                    key_nodes.append(node.args[key_index])
                key_nodes.extend(keyword.value for keyword in node.keywords if keyword.arg == "message_key")
                calls.extend(
                    (node.lineno, key_node.value, plural)
                    for key_node in key_nodes
                    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)
                )
            for line, key, plural in sorted(calls):
                if not I18N_COMPLETE_LITERAL_KEY_RE.fullmatch(key):
                    continue
                expected = [f"{key}.one", f"{key}.other"] if plural else [key]
                missing = [candidate for candidate in expected if candidate not in source]
                if missing:
                    errors.append(f"{display_path}:{line} missing i18n key(s): {', '.join(missing)}")
            continue
        for match in I18N_LITERAL_CALL_RE.finditer(text):
            key = match.group("key")
            if not I18N_COMPLETE_LITERAL_KEY_RE.fullmatch(key):
                continue
            expected = [f"{key}.one", f"{key}.other"] if match.group("function") == "tPlural" else [key]
            missing = [candidate for candidate in expected if candidate not in source]
            if not missing:
                continue
            line = text.count("\n", 0, match.start()) + 1
            errors.append(f"{display_path}:{line} missing i18n key(s): {', '.join(missing)}")
    return errors


def i18n_template_literal_text(value: str) -> str:
    """Return only the user-visible literal chunks from a JavaScript template body."""
    output: list[str] = []
    index = 0
    depth = 0
    while index < len(value):
        if depth == 0 and value.startswith("${", index):
            depth = 1
            index += 2
            continue
        char = value[index]
        if depth:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def i18n_visible_literal_sink_errors(paths: list[Path] | None = None) -> list[str]:
    """Reject direct English literals at common user-visible DOM/status sinks."""
    errors: list[str] = []
    source_paths = paths if paths is not None else [
        *(REPO_ROOT / path for path in ASSETS["yolomux.js"]),
        *(REPO_ROOT / path for path in ASSETS["yolomux.css"]),
    ]
    for path in source_paths:
        text = read_text(path)
        display_path = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path.name
        patterns = I18N_VISIBLE_LITERAL_SINK_PATTERNS
        if path.suffix == ".js":
            patterns += I18N_VISIBLE_TEMPLATE_SINK_PATTERNS
        elif path.suffix == ".css":
            patterns = (I18N_CSS_CONTENT_RE,)
        for pattern in patterns:
            for match in pattern.finditer(text):
                value = match.group("value").strip()
                literal_value = i18n_template_literal_text(value) if pattern in I18N_VISIBLE_TEMPLATE_SINK_PATTERNS else value
                literal_value = re.sub(r"\\[nrt]", "", literal_value)
                words = [word.lower() for word in re.findall(r"[A-Za-z]+", literal_value)]
                if not words or (len(words) == 1 and len(words[0]) == 1) or all(word in I18N_VISIBLE_LITERAL_UNITS for word in words):
                    continue
                if i18n_value_intentionally_identical("", literal_value):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{display_path}:{line} visible literal bypasses i18n: {value!r}")
        if path.suffix == ".js":
            lines = text.splitlines()
            for line_number, line_text in enumerate(lines, start=1):
                if not I18N_VISIBLE_SINK_RE.search(line_text):
                    continue
                if any(parent in line_text for parent in ("dropActionDisplayLabel", "messageDescriptorText", "pullRequestCiStatusDisplay", "userMessageText")):
                    continue
                for match in I18N_VISIBLE_RAW_FIELD_RE.finditer(line_text):
                    next_line = lines[line_number].lstrip() if line_number < len(lines) else ""
                    if line_text.rstrip().endswith(match.group("value")) and next_line.startswith("?"):
                        continue
                    if "? t(" in line_text or "? localizedHtml(" in line_text:
                        continue
                    errors.append(f"{display_path}:{line_number} raw user-visible field bypasses i18n: {match.group('value')!r}")
    return sorted(errors)


def i18n_value_intentionally_identical(key: str, value: str, locale: str = "") -> bool:
    """Return true for strings that should stay identical across locales."""
    text = str(value or "").strip()
    if not text:
        return True
    # Placeholders, shortcuts, punctuation, counts, and glyph-only labels do not need translation.
    without_tokens = re.sub(r"\{\w+\}", "", text)
    if not re.search(r"[A-Za-z]", without_tokens):
        return True
    normalized = re.sub(r"[^A-Za-z0-9!+.#_-]+", " ", without_tokens).strip().lower()
    if not normalized:
        return True
    parts = [part.strip(".…:;!?") for part in normalized.split() if part.strip(".…:;!?")]
    if parts and all(part in I18N_ALLOWED_IDENTICAL_TERMS for part in parts):
        return True
    if normalized in {"a-z", "z-a"}:
        return True
    if normalized == "keiven chang":
        return True
    if key in I18N_ALLOWED_IDENTICAL_KEYS:
        return True
    if key in I18N_ALLOWED_IDENTICAL_LOCALE_KEYS.get(locale, frozenset()):
        return True
    # Brand/legal strings are intentionally stable but may contain version numbers or spaces.
    if re.fullmatch(r"(?:polyform noncommercial license|apache license|mit license)(?: [0-9.]+)?", normalized):
        return True
    if key.endswith(".shortcut") or key.endswith(".key"):
        return True
    return False


def i18n_untranslated_entries(catalogs: dict[str, dict]) -> dict[str, list[str]]:
    """Keys whose localized value is still byte-identical to the English source."""
    source = catalogs.get(SOURCE_LOCALE) or {}
    result: dict[str, list[str]] = {}
    for locale, catalog in sorted(catalogs.items()):
        if locale == SOURCE_LOCALE:
            continue
        entries: list[str] = []
        for key in sorted(locale_expected_keys(source, locale)):
            source_key = locale_source_key(source, key)
            english_value = source.get(source_key)
            value = catalog.get(key)
            if value != english_value:
                continue
            if i18n_value_intentionally_identical(key, str(english_value), locale):
                continue
            entries.append(key)
        result[locale] = entries
    return result


def i18n_untranslated_report(catalogs: dict[str, dict] | None = None, sample_limit: int | None = I18N_UNTRANSLATED_REPORT_SAMPLE_LIMIT) -> tuple[list[str], list[str]]:
    """Return warning lines plus errors for every unintended source-equal value."""
    entries = i18n_untranslated_entries(catalogs or source_catalogs())
    warnings: list[str] = []
    errors: list[str] = []
    for locale, keys in entries.items():
        count = len(keys)
        if count:
            shown = keys if sample_limit is None else keys[:max(0, sample_limit)]
            suffix = "" if sample_limit is None or len(keys) <= sample_limit else f" (+{len(keys) - sample_limit} more)"
            warnings.append(f"WARNING: i18n untranslated values in {locale}.json: {count}; keys: {', '.join(shown)}{suffix}")
            errors.append(f"{locale}.json has {count} unintended English fallback value(s)")
    return warnings, errors


def i18n_placeholder_tokens(value: object) -> Counter[str]:
    """Interpolation placeholders are an exact multiset contract with the source locale."""
    return Counter(I18N_PLACEHOLDER_RE.findall(str(value or "")))


def i18n_protected_tokens(value: object, key: str = "") -> Counter[str]:
    """Return URL, code, path, tag, and shortcut tokens that must stay byte-for-byte."""
    tokens = []
    for match in I18N_PROTECTED_TOKEN_RE.finditer(str(value or "")):
        token = match.group(0).rstrip(".,;:")
        if key in I18N_TRANSLATABLE_CODE_KEYS and token.startswith("`"):
            continue
        tokens.append(token)
    return Counter(tokens)


def i18n_duplicate_ownership_errors(source: dict[str, object]) -> list[str]:
    """Reject source-locale synonyms that bypass one neutral semantic owner."""
    plural_categories = set().union(*PLURAL_CATEGORIES_BY_LOCALE.values())
    plural_keys = {
        f"{base}.{category}"
        for base in plural_family_bases(source)
        for category in plural_categories
        if f"{base}.{category}" in source
    }
    keys_by_value: dict[str, list[str]] = defaultdict(list)
    for key, raw_value in source.items():
        value = str(raw_value or "").strip()
        if value and key not in plural_keys:
            keys_by_value[value].append(key)
    errors: list[str] = []
    for value, keys in sorted(keys_by_value.items()):
        if len(keys) < 2:
            continue
        group = frozenset(keys)
        if group in I18N_ALLOWED_DUPLICATE_KEY_GROUPS:
            continue
        errors.append(
            f"{SOURCE_LOCALE}.json duplicate locale concept lacks a shared owner: "
            f"{', '.join(sorted(group))} = {value!r}"
        )
    plural_families_by_value: dict[tuple[object, object], list[str]] = defaultdict(list)
    for base in plural_family_bases(source):
        plural_families_by_value[(source.get(f"{base}.one"), source.get(f"{base}.other"))].append(base)
    for values, bases in sorted(plural_families_by_value.items(), key=lambda item: str(item[0])):
        if len(bases) < 2:
            continue
        group = frozenset(bases)
        if group in I18N_ALLOWED_DUPLICATE_PLURAL_FAMILY_GROUPS:
            continue
        errors.append(
            f"{SOURCE_LOCALE}.json duplicate plural locale concept lacks a shared owner: "
            f"{', '.join(sorted(group))} = {values!r}"
        )
    return errors


def locale_semantic_errors(catalogs: dict[str, dict]) -> list[str]:
    """Validate one shared semantic contract for every translated catalog."""
    errors = [*locale_registry_errors(catalogs), *locale_key_errors(catalogs)]
    source = catalogs.get(SOURCE_LOCALE) or {}
    errors.extend(i18n_duplicate_ownership_errors(source))
    for locale, catalog in sorted(catalogs.items()):
        if locale == SOURCE_LOCALE:
            continue
        required_yo_marker = I18N_REQUIRED_YO_MARKERS.get(locale, "")
        for key in sorted(locale_expected_keys(source, locale)):
            if key not in catalog:
                continue
            source_key = locale_source_key(source, key)
            english_value = source.get(source_key)
            value = catalog[key]
            if str(english_value or "").strip() and not str(value or "").strip():
                errors.append(f"{locale}.json blank translation at {key}")
            expected_placeholders = i18n_placeholder_tokens(english_value)
            actual_placeholders = i18n_placeholder_tokens(value)
            if actual_placeholders != expected_placeholders:
                errors.append(
                    f"{locale}.json placeholder drift at {key}: expected {dict(expected_placeholders)}, got {dict(actual_placeholders)}"
                )
            expected_protected = i18n_protected_tokens(english_value, key)
            actual_protected = i18n_protected_tokens(value, key)
            if actual_protected != expected_protected:
                errors.append(
                    f"{locale}.json protected-token drift at {key}: expected {dict(expected_protected)}, got {dict(actual_protected)}"
                )
            if required_yo_marker and "YO" in str(english_value) and ("YO" in str(value) or required_yo_marker not in str(value)):
                errors.append(f"{locale}.json must localize YO as {required_yo_marker} at {key}")
    _warnings, untranslated_errors = i18n_untranslated_report(catalogs, sample_limit=None)
    errors.extend(untranslated_errors)
    return errors


def pseudo_value(value: str) -> str:
    """Accent prose while keeping placeholders, code, URLs, paths, tags, and shortcuts byte-exact."""
    segments = I18N_PSEUDO_TOKEN_RE.split(str(value))
    accented = "".join(seg if I18N_PSEUDO_TOKEN_RE.fullmatch(seg) else seg.translate(_PSEUDO_ACCENTS) for seg in segments)
    visible = I18N_PSEUDO_TOKEN_RE.sub("", str(value))
    pad = "·" * max(1, round(len(visible) * 0.4))
    return f"⟦{accented}{pad}⟧"


def build_pseudo_catalog(source: dict) -> dict:
    return {key: pseudo_value(value) for key, value in source.items()}


def expected_locale_outputs() -> dict[str, str]:
    """Map of static/locales/<locale>.json -> JSON text the build should produce."""
    catalogs = source_catalogs()
    errors = locale_semantic_errors(catalogs)
    errors.extend(i18n_literal_key_errors(catalogs.get(SOURCE_LOCALE) or {}))
    errors.extend(i18n_visible_literal_sink_errors())
    if errors:
        raise BuildError("i18n semantic check failed:\n  " + "\n  ".join(errors))
    outputs: dict[str, str] = {}
    for locale, catalog in catalogs.items():
        outputs[locale] = json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    source = catalogs.get(SOURCE_LOCALE)
    if source is not None:
        outputs[PSEUDO_LOCALE] = json.dumps(build_pseudo_catalog(source), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return outputs


def write_locales() -> bool:
    outputs = expected_locale_outputs()
    if not outputs:
        return False
    LOCALES_OUT.mkdir(parents=True, exist_ok=True)
    changed = False
    for locale, text in outputs.items():
        path = LOCALES_OUT / f"{locale}.json"
        try:
            current = read_text(path)
        except FileNotFoundError:
            current = None
        if current != text:
            path.write_text(text, encoding="utf-8")
            changed = True
    return changed


def lint_duplicate_functions() -> list[str]:
    """Return error lines for top-level declarations made in more than one JS source file.

    The partials concatenate into one bundle sharing one scope, so a name declared in two partials lets the
    last-concatenated one win silently (AGENTS.md). this covers top-level `const`/`let`/`class`
    too, not just `function` — a duplicate const would otherwise only surface as a `node --check`
    redeclaration error, which the manual lane can miss.
    """
    js_sources = ASSETS.get("yolomux.js", [])
    name_to_files: dict[str, list[str]] = defaultdict(list)
    decl = re.compile(r"^(?:function\s+(\w+)\s*\(|(?:const|let|class)\s+(\w+)\b)")
    for part in js_sources:
        path = repo_path(part)
        try:
            text = read_text(path)
        except FileNotFoundError:
            continue
        for line in text.splitlines():
            m = decl.match(line)
            if m:
                name_to_files[m.group(1) or m.group(2)].append(str(part))
    errors = []
    for name, files in sorted(name_to_files.items()):
        if len(files) > 1:
            errors.append(f"duplicate top-level declaration '{name}' in: {', '.join(files)}")
    return errors


def lint_direct_button_construction() -> list[str]:
    """Runtime buttons must inherit the shared type/ARIA/dataset/event contract from makeButton()."""
    errors: list[str] = []
    owner_count = 0
    function_re = re.compile(r"^function\s+(\w+)\s*\(")
    construction_re = re.compile(r"document\.createElement\(\s*(['\"])button\1\s*\)")
    for part in ASSETS.get("yolomux.js", []):
        path = repo_path(part)
        try:
            lines = read_text(path).splitlines()
        except FileNotFoundError:
            continue
        current_function = ""
        for line_number, line in enumerate(lines, start=1):
            match = function_re.match(line)
            if match:
                current_function = match.group(1)
            if not construction_re.search(line):
                continue
            if part.endswith("10_core_utils.js") and current_function == "makeButton":
                owner_count += 1
                continue
            errors.append(f"{part}:{line_number}: direct button construction bypasses makeButton()")
    if owner_count != 1:
        errors.append(f"shared makeButton() must own exactly one direct button construction; found {owner_count}")
    return errors


def lint_shared_ui_ownership() -> list[str]:
    """Reject a second owner for the refactored panel/control/editor/chart contracts."""
    errors: list[str] = []
    for part, requirements in SHARED_UI_OWNERSHIP_REQUIREMENTS.items():
        path = repo_path(part)
        try:
            source = read_text(path)
        except FileNotFoundError:
            continue
        for requirement in requirements:
            label, *needles = requirement
            missing = [needle for needle in needles if needle not in source]
            if missing:
                errors.append(f"{part}: shared {label} owner is missing {', '.join(repr(needle) for needle in missing)}")
    for part in ASSETS.get("yolomux.js", []):
        path = repo_path(part)
        try:
            source = read_text(path)
        except FileNotFoundError:
            continue
        for label, needle in SHARED_UI_OWNERSHIP_FORBIDDEN_NEEDLES:
            if needle in source:
                errors.append(f"{part}: shared ownership forbids {label} ({needle!r})")
    terminal_boot = repo_path("static_src/js/yolomux/99_terminal_boot.js")
    if terminal_boot.exists():
        source = read_text(terminal_boot)
        frame_start = source.find("function paneFrameControlsHtml")
        frame_end = source.find("\nfunction ", frame_start + 1)
        frame_body = source[frame_start:frame_end if frame_end >= 0 else len(source)]
        if "<button" in frame_body:
            errors.append("static_src/js/yolomux/99_terminal_boot.js: paneFrameControlsHtml() must use toolbarButtonHtml(), not raw button templates")
    for path in sorted((REPO_ROOT / "yolomux_lib").rglob("*.py")):
        source = read_text(path)
        if "return self.yoagent_controller." in source:
            errors.append(f"{path.relative_to(REPO_ROOT)}: YO!agent forwarding wrappers must call the controller at the caller, not return through the app")
    return errors


def normalized_production_lines(source: str) -> list[str]:
    """Keep statement shape while removing comments, literal values, and incidental whitespace."""
    result: list[str] = []
    for raw in source.splitlines():
        line = re.sub(r"//.*$|#.*$", "", raw).strip()
        if not line or line in {"{", "}", "};"}:
            continue
        # Signatures, export lists, and argument continuations are structural scaffolding, not
        # copied behavior.  Ignoring them keeps the ratchet focused on executable owners.
        if line.startswith(("def ", "class ", "@", "__all__")) or line.endswith(","):
            continue
        line = re.sub(r"(['\"]).*?\1", "<str>", line)
        line = re.sub(r"\b\d+(?:\.\d+)?\b", "<num>", line)
        line = re.sub(r"\s+", " ", line)
        result.append(line)
    return result


def lint_normalized_production_clones(window: int = 8) -> list[str]:
    """Ratchet meaningful cross-file copy/paste blocks without scanning generated/test assets."""
    windows: dict[str, set[str]] = defaultdict(set)
    sources = [*ASSETS.get("yolomux.js", [])]
    sources += [str(path.relative_to(REPO_ROOT)) for path in sorted((REPO_ROOT / "yolomux_lib").rglob("*.py"))]
    for part in sources:
        try:
            lines = normalized_production_lines(read_text(repo_path(part)))
        except FileNotFoundError:
            continue
        for index in range(max(0, len(lines) - window + 1)):
            block = lines[index:index + window]
            # Generic guards/returns are not an ownership problem; a clone must carry a call,
            # assignment, or property access in addition to its shared structural shape.
            if not any("(" in line or "=" in line or "." in line for line in block):
                continue
            digest = hashlib.sha1("\n".join(block).encode("utf-8")).hexdigest()[:12]
            windows[digest].add(part)
    errors: list[str] = []
    for digest, parts in sorted(windows.items()):
        if len(parts) < 2:
            continue
        files = ", ".join(sorted(parts))
        key = f"{files}:{digest}"
        if key not in NORMALIZED_PRODUCTION_CLONE_ALLOWLIST:
            errors.append(f"normalized production clone {digest} across {files}; route both copies through one owner or add a reviewed allowlist reason")
    return errors


def lint_source_control_characters() -> list[str]:
    """Static source partials must remain normal UTF-8 text that grep, editors, and linters can inspect."""
    errors: list[str] = []
    seen: set[str] = set()
    for parts in ASSETS.values():
        for part in parts:
            if part in seen:
                continue
            seen.add(part)
            path = repo_path(part)
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                continue
            for offset, value in enumerate(data):
                if value >= 32 or value in (9, 10, 13):
                    continue
                line = data.count(b"\n", 0, offset) + 1
                line_start = data.rfind(b"\n", 0, offset) + 1
                column = offset - line_start + 1
                errors.append(f"{part}:{line}:{column}: source control character U+{value:04X}; use a textual escape or structured signature")
    return errors


def lint_asset_source_completeness() -> list[str]:
    """Every source partial on disk must be shipped or explicitly retired with a reason."""
    source_families = (
        ("static_src/js/yolomux", "*.js"),
        ("static_src/js/emoji", "*.js"),
        ("static_src/css/yolomux", "*.css"),
    )
    discovered = {
        f"{directory}/{path.name}"
        for directory, pattern in source_families
        for path in repo_path(directory).glob(pattern)
        if path.is_file()
    }
    registrations = [str(Path(part).as_posix()) for parts in ASSETS.values() for part in parts]
    registered = set(registrations)
    retired = {str(Path(part).as_posix()) for part in RETIRED_ASSET_PARTS}
    errors: list[str] = []

    for part, count in sorted(Counter(registrations).items()):
        if count > 1:
            errors.append(f"static asset source part registered {count} times: {part}")
    for part in sorted(registered - discovered):
        errors.append(f"registered static asset source part is missing: {part}")
    for part, reason in sorted(RETIRED_ASSET_PARTS.items()):
        normalized = str(Path(part).as_posix())
        if not str(reason).strip():
            errors.append(f"retired static asset part {normalized} needs a nonblank reviewed reason")
        if normalized not in discovered:
            errors.append(f"retired static asset part is missing: {normalized}; remove stale marker")
        if normalized in registered:
            errors.append(f"retired static asset part is also registered: {normalized}; remove retired marker")
    for part in sorted(discovered - registered - retired):
        errors.append(
            f"unregistered static asset source part {part}; add it to ASSETS or "
            "RETIRED_ASSET_PARTS with a reviewed reason"
        )
    return errors


def lint_undefined_css_vars() -> list[str]:
    """Every `var(--x)` in the CSS bundle must resolve to a `--x:` definition (CSS) or a JS
    `setProperty('--x', …)` / inline `style="--x:…"`. a typo'd or removed token name otherwise
    degrades silently (a hover that does nothing, an invalid `font` shorthand)."""
    css = build_asset("yolomux.css")
    js = build_asset("yolomux.js")
    css_no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    defined = set(re.findall(r"(--[\w-]+)\s*:", css_no_comments))
    defined |= set(re.findall(r"setProperty\(\s*[`'\"]\s*(--[\w-]+)", js))
    defined |= set(re.findall(r"(--[\w-]+)\s*:", js))
    referenced = set(re.findall(r"var\(\s*(--[\w-]+)", css_no_comments))
    return [f"undefined CSS var {name} (referenced via var() but never defined in CSS or set from JS)"
            for name in sorted(referenced - defined)]


def lint_unused_css_tokens() -> list[str]:
    """Reject newly defined CSS custom properties that no CSS var() or JavaScript references."""
    css = _css_without_comments(build_asset("yolomux.css"))
    js = build_asset("yolomux.js")
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    referenced = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    referenced.update(re.findall(r"--[\w-]+", js))
    unused = defined - referenced
    errors: list[str] = []

    for token, reason in sorted(UNUSED_CSS_TOKEN_ALLOWLIST.items()):
        if not re.fullmatch(r"--[\w-]+", token):
            errors.append(f"invalid unused CSS token allowlist entry {token}")
            continue
        if not str(reason).strip():
            errors.append(f"unused CSS token allowlist entry {token} needs a nonblank reviewed reason")
        if token not in defined:
            errors.append(f"stale unused CSS token allowlist entry {token}: token is no longer defined")
        elif token not in unused:
            errors.append(f"stale unused CSS token allowlist entry {token}: token is now referenced")
    for token in sorted(unused - set(UNUSED_CSS_TOKEN_ALLOWLIST)):
        errors.append(
            f"unused CSS token {token}; remove it or add UNUSED_CSS_TOKEN_ALLOWLIST with a reviewed reason"
        )
    return errors


def _token_opaque_color_values() -> dict[str, list[str]]:
    token_file = repo_path(_css_token_source_part())
    css = re.sub(r"/\*.*?\*/", "", read_text(token_file), flags=re.DOTALL)
    values: dict[str, list[str]] = defaultdict(list)
    literal_re = re.compile(rf"(--[\w-]+)\s*:\s*({CSS_COLOR_LITERAL_PATTERN})")
    for match in literal_re.finditer(css):
        canonical = _canonical_opaque_color(match.group(2))
        if canonical:
            values[canonical].append(match.group(1))
    return values


def _css_token_source_part() -> str:
    """Return the registered token partial so synthetic ASSETS fixtures use the same code path."""
    return next(
        (
            part
            for part in ASSETS.get("yolomux.css", [])
            if str(part).endswith("00_tokens_base.css")
        ),
        "static_src/css/yolomux/00_tokens_base.css",
    )


def _token_color_identities() -> set[tuple[int, int, int, float]]:
    """All literal color identities owned by the shared token partial, including alpha colors."""
    css = _css_without_comments(read_text(repo_path(_css_token_source_part())))
    identities: set[tuple[int, int, int, float]] = set()
    declaration_re = re.compile(r"(?:^|[;{])\s*--[\w-]+\s*:\s*([^;{}]+)")
    for declaration in declaration_re.finditer(css):
        for match in CSS_COLOR_LITERAL_RE.finditer(declaration.group(1)):
            identity = _color_identity(match.group(0))
            if identity is not None:
                identities.add(identity)
    return identities


def lint_novel_component_colors() -> list[str]:
    """A raw component color must be token-owned or part of the reviewed migration baseline."""
    token_identities = _token_color_identities()
    css_parts = set(ASSETS.get("yolomux.css", []))
    occurrences: dict[str, dict[tuple[int, int, int, float], list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    errors: list[str] = []

    for part in sorted(css_parts):
        if part.endswith("00_tokens_base.css"):
            continue
        try:
            text = _css_without_var_functions(
                _css_without_custom_property_values(
                    _css_without_comments(read_text(repo_path(part)))
                )
            )
        except FileNotFoundError:
            continue
        for match in CSS_COLOR_LITERAL_RE.finditer(text):
            identity = _color_identity(match.group(0))
            if identity is None or identity in token_identities:
                continue
            line_no = 1 + text.count("\n", 0, match.start())
            occurrences[part][identity].append(line_no)

    allowed_by_part: dict[str, set[tuple[int, int, int, float]]] = defaultdict(set)
    for part, entry in sorted(NOVEL_COMPONENT_COLOR_ALLOWLIST.items()):
        colors, reason = entry
        if part not in css_parts:
            continue
        if not str(reason).strip():
            errors.append(f"novel component color allowlist for {part} needs a nonblank reviewed reason")
        for literal in sorted(colors):
            identity = _color_identity(literal)
            if identity is None:
                errors.append(f"invalid novel component color allowlist entry {literal} for {part}")
                continue
            allowed_by_part[part].add(identity)
            if identity not in occurrences.get(part, {}):
                errors.append(
                    f"stale novel component color allowlist entry {literal} for {part}; remove it"
                )

    for part, identities in sorted(occurrences.items()):
        for identity, lines in sorted(identities.items()):
            if identity in allowed_by_part.get(part, set()):
                continue
            literal = _color_identity_label(identity)
            shown = ", ".join(str(line) for line in lines[:4])
            suffix = "" if len(lines) <= 4 else f" (+{len(lines) - 4} more)"
            errors.append(
                f"{part}:{shown}: novel raw component color {literal}{suffix}; use a CSS token or "
                "add NOVEL_COMPONENT_COLOR_ALLOWLIST with a reviewed reason"
            )
    return errors


def _css_custom_property_declarations(body: str) -> dict[str, str]:
    declaration_re = re.compile(r"(?:^|;)\s*(--[\w-]+)\s*:\s*([^;{}]+)")
    return {match.group(1): match.group(2).strip() for match in declaration_re.finditer(body)}


def _resolve_literal_color_token(token: str, values: dict[str, str]) -> str | None:
    seen: set[str] = set()
    current = token
    while current not in seen:
        seen.add(current)
        value = values.get(current, "").strip()
        reference = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", value)
        if reference is None:
            return value if _color_luminance_alpha(value) is not None else None
        current = reference.group(1)
    return None


def lint_semantic_color_contrast() -> list[str]:
    """Enforce WCAG normal-text contrast for the core semantic foreground/background pairs."""
    token_path = repo_path(_css_token_source_part())
    try:
        css = _css_without_comments(read_text(token_path))
    except FileNotFoundError:
        return [f"semantic contrast token source is missing: {token_path}"]
    dark: dict[str, str] = {}
    light_overrides: dict[str, str] = {}
    for _context, selector, body, _line in _iter_located_css_rules(css):
        members = set(_split_css_selector_list(selector))
        if ":root" in members:
            dark.update(_css_custom_property_declarations(body))
        if "body.theme-light" in members:
            light_overrides.update(_css_custom_property_declarations(body))

    errors: list[str] = []
    for theme, values in (("dark", dark), ("light", {**dark, **light_overrides})):
        for foreground, background in SEMANTIC_CONTRAST_PAIRS:
            foreground_color = _resolve_literal_color_token(foreground, values)
            background_color = _resolve_literal_color_token(background, values)
            foreground_measure = _color_luminance_alpha(foreground_color or "")
            background_measure = _color_luminance_alpha(background_color or "")
            if (
                foreground_measure is None
                or background_measure is None
                or not math.isclose(foreground_measure[1], 1.0)
                or not math.isclose(background_measure[1], 1.0)
            ):
                errors.append(
                    f"{theme} contrast {foreground} on {background} cannot resolve to opaque literal colors"
                )
                continue
            ratio = (
                max(foreground_measure[0], background_measure[0]) + 0.05
            ) / (min(foreground_measure[0], background_measure[0]) + 0.05)
            if ratio < 4.5:
                errors.append(
                    f"{theme} contrast {foreground} ({foreground_color}) on {background} "
                    f"({background_color}) is {ratio:.2f}:1; expected >= 4.50:1"
                )
    return errors


def lint_raw_literal_equals_token() -> list[str]:
    """Semantic token colors should be referenced through `var(--token)`, not copied as raw hex.

    `var(--x, #fallback)` literals are intentionally left alone because the fallback belongs to the token
    reference. Opaque white has an explicit paint owner and is checked even inside local custom properties.
    """
    token_values = _token_opaque_color_values()
    ignored_values = {_canonical_opaque_color(value) for value in RAW_TOKEN_LITERAL_IGNORED_VALUES}
    errors: list[str] = []
    for part in ASSETS.get("yolomux.css", []):
        if part.endswith("00_tokens_base.css"):
            continue
        path = repo_path(part)
        try:
            text = _css_without_var_functions(_css_without_comments(read_text(path)))
        except FileNotFoundError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            local_custom_property = re.match(r"^\s*--[\w-]+\s*:", line) is not None
            for match in CSS_COLOR_LITERAL_RE.finditer(line):
                literal = match.group(0)
                canonical = _canonical_opaque_color(literal)
                if local_custom_property and canonical != "#ffffff":
                    continue
                if canonical not in token_values:
                    continue
                if canonical in ignored_values:
                    continue
                tokens = ", ".join(sorted(set(token_values[canonical])))
                errors.append(f"{part}:{line_no}: raw color {literal.lower()} duplicates token value(s) {tokens}; use var(--token)")
    return errors


def lint_repeated_raw_component_literals() -> list[str]:
    """Equivalent repeated component color literals need one owner, regardless of CSS spelling."""
    occurrences: dict[tuple[int, int, int, float], list[str]] = defaultdict(list)
    variable_occurrences: dict[tuple[str, float], list[str]] = defaultdict(list)
    variable_color_re = re.compile(
        r"rgba?\(\s*var\(\s*(--[\w-]+)\s*\)\s*/\s*([0-9]*\.?[0-9]+%?)\s*\)",
        re.IGNORECASE,
    )
    for part in ASSETS.get("yolomux.css", []):
        if part.endswith("00_tokens_base.css"):
            continue
        path = repo_path(part)
        try:
            source = _css_without_comments(read_text(path))
        except FileNotFoundError:
            continue
        for match in variable_color_re.finditer(source):
            raw_alpha = match.group(2)
            alpha = float(raw_alpha.rstrip("%")) / (100 if raw_alpha.endswith("%") else 1)
            identity = (match.group(1).lower(), round(max(0.0, min(1.0, alpha)), 6))
            line_no = 1 + source.count("\n", 0, match.start())
            variable_occurrences[identity].append(f"{part}:{line_no}")
        text = _css_without_var_functions(source)
        for match in CSS_COLOR_LITERAL_RE.finditer(text):
            identity = _color_identity(match.group(0))
            if identity is None:
                continue
            line_no = 1 + text.count("\n", 0, match.start())
            occurrences[identity].append(f"{part}:{line_no}")
    errors: list[str] = []
    allowlist: dict[tuple[int, int, int, float], str] = {}
    for literal in sorted(RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST):
        identity = _color_identity(literal)
        if identity is None:
            errors.append(f"invalid raw component color allowlist entry {literal}")
            continue
        allowlist[identity] = literal
        count = len(occurrences.get(identity, []))
        if count < 2:
            errors.append(f"stale raw component color allowlist entry {literal}: found {count} occurrence(s); remove it")
    for identity, locations in sorted(occurrences.items()):
        if len(locations) < 2:
            continue
        if identity in allowlist:
            continue
        literal = _color_identity_label(identity)
        shown = ", ".join(locations[:4])
        suffix = "" if len(locations) <= 4 else f" (+{len(locations) - 4} more)"
        errors.append(f"raw component color {literal} repeats in {shown}{suffix}; move it to a CSS token or add a reviewed allowlist reason")
    for (variable, alpha), locations in sorted(variable_occurrences.items()):
        if len(locations) < 2:
            continue
        shown = ", ".join(locations[:4])
        suffix = "" if len(locations) <= 4 else f" (+{len(locations) - 4} more)"
        errors.append(
            f"raw component color rgb(var({variable}) / {alpha:g}) repeats in {shown}{suffix}; "
            "move it to a CSS token"
        )
    return errors


def lint_repeated_raw_box_shadows() -> list[str]:
    """Repeated shadow geometry belongs in one token or grouped selector."""
    occurrences: dict[str, list[str]] = defaultdict(list)
    declaration_re = re.compile(r"(?:^|;)\s*box-shadow\s*:\s*([^;}]+)", re.IGNORECASE)
    for part in ASSETS.get("yolomux.css", []):
        if part.endswith("00_tokens_base.css"):
            continue
        path = repo_path(part)
        try:
            css = _css_without_comments(read_text(path))
        except FileNotFoundError:
            continue
        for _context, _selector, body, rule_line in _iter_located_css_rules(css):
            for declaration in declaration_re.finditer(body):
                value = " ".join(declaration.group(1).split())
                unimportant = re.sub(r"\s*!important\s*$", "", value, flags=re.IGNORECASE)
                if unimportant == "none" or re.fullmatch(r"var\(\s*--[\w-]+\s*\)", unimportant):
                    continue
                line_no = rule_line + body.count("\n", 0, declaration.start())
                occurrences[value].append(f"{part}:{line_no}")
    errors: list[str] = []
    for value, locations in sorted(occurrences.items()):
        if len(locations) < 2:
            continue
        errors.append(
            f"raw box-shadow {value!r} repeats in {', '.join(locations)}; "
            "move it to a CSS token or grouped selector"
        )
    return errors


def lint_unowned_z_indexes() -> list[str]:
    """Every stacking declaration must consume one named z-index token.

    Component-side literals and arithmetic split the layer graph across CSS and inline JS styles.
    Keep numeric ownership and relationships in the token partial; consumers use a direct --z-* var.
    """
    errors: list[str] = []
    declaration_re = re.compile(r"\bz-index\s*:\s*([^;}]+)", re.IGNORECASE)
    token_definition_re = re.compile(r"(--z-[\w-]+)\s*:\s*([^;}]+)", re.IGNORECASE)
    owned_re = re.compile(r"var\(\s*--z-[\w-]+\s*\)(?:\s*!important)?", re.IGNORECASE)
    css_wide_re = re.compile(r"(?:auto|inherit|initial|revert|revert-layer|unset)(?:\s*!important)?", re.IGNORECASE)
    for asset in ("yolomux.css", "yolomux.js"):
        for part in ASSETS.get(asset, []):
            path = repo_path(part)
            try:
                source = _css_without_comments(read_text(path))
            except FileNotFoundError:
                continue
            if not part.endswith("00_tokens_base.css"):
                for definition in token_definition_re.finditer(source):
                    name = definition.group(1)
                    value = " ".join(definition.group(2).split())
                    if owned_re.fullmatch(value):
                        continue
                    line_no = 1 + source.count("\n", 0, definition.start())
                    errors.append(
                        f"{part}:{line_no}: local z-index token {name} owns {value!r}; "
                        "move numeric/arithmetic ownership to 00_tokens_base.css"
                    )
            for declaration in declaration_re.finditer(source):
                value = " ".join(declaration.group(1).split())
                if owned_re.fullmatch(value) or css_wide_re.fullmatch(value):
                    continue
                line_no = 1 + source.count("\n", 0, declaration.start())
                errors.append(
                    f"{part}:{line_no}: z-index {value!r} lacks one named --z-* owner; "
                    "move literals/arithmetic to 00_tokens_base.css and use var(--z-token)"
                )
    return errors


def lint_raw_window_viewport_reads() -> list[str]:
    """Every JS viewport read must go through appViewport(); the one owner line is marked."""
    errors: list[str] = []
    pattern = re.compile(r"\bwindow\.inner(?:Width|Height)\b")
    for part in ASSETS.get("yolomux.js", []):
        path = repo_path(part)
        try:
            text = read_text(path)
        except FileNotFoundError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not pattern.search(line):
                continue
            if WINDOW_VIEWPORT_ALLOW_MARKER in line:
                continue
            errors.append(f"{part}:{line_no}: raw window.innerWidth/innerHeight read; use appViewport()")
    return errors


def check_locales() -> list[str]:
    stale: list[str] = []
    for locale, text in expected_locale_outputs().items():
        path = LOCALES_OUT / f"{locale}.json"
        try:
            current = read_text(path)
        except FileNotFoundError:
            current = None
        if current != text:
            stale.append(f"locales/{locale}.json")
    return stale


def watched_source_paths(assets: list[str]) -> list[Path]:
    """Every source file whose change should trigger a rebuild: the asset partials + locale catalogs."""
    paths: list[Path] = []
    for asset in assets:
        paths.extend(repo_path(part) for part in ASSETS[asset])
    if LOCALES_SRC.is_dir():
        paths.extend(sorted(LOCALES_SRC.glob("*.json")))
    return paths


def build_once(assets: list[str]) -> list[str]:
    check_css_braces()
    changed = [asset for asset in assets if write_asset(asset)]
    if write_locales():
        changed.append("locales")
    return changed


def watch_loop(assets: list[str], interval: float = 0.4) -> int:
    """Dev-velocity #1: rebuild on source change so the edit->reload loop drops the manual build step.
    A plain mtime poll (the build is ~36ms; no inotify dependency). A BuildError mid-edit (e.g. a
    transient locale-parity gap while typing) is printed and the watch keeps running."""
    sources = watched_source_paths(assets)
    print(f"watching {len(sources)} source files (Ctrl-C to stop); building {', '.join(assets)}", flush=True)
    try:
        changed = build_once(assets)
        print("rebuilt: " + (", ".join(changed) if changed else "(up to date)"), flush=True)
    except BuildError as exc:
        print(str(exc), file=sys.stderr, flush=True)

    def snapshot() -> dict[Path, float]:
        stamps: dict[Path, float] = {}
        for path in sources:
            try:
                stamps[path] = path.stat().st_mtime_ns
            except OSError:
                stamps[path] = 0
        return stamps

    last = snapshot()
    try:
        while True:
            time.sleep(interval)
            now = snapshot()
            if now == last:
                continue
            last = now
            try:
                changed = build_once(assets)
                if changed:
                    print("rebuilt: " + ", ".join(changed), flush=True)
            except BuildError as exc:
                print(str(exc), file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        print("\nwatch stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", nargs="*", choices=sorted(ASSETS), help="assets to build; defaults to all")
    parser.add_argument("--check", action="store_true", help="fail if generated static files are stale")
    parser.add_argument("--watch", action="store_true", help="rebuild on source change (mtime poll) until interrupted")
    parser.add_argument("--lint-light", action="store_true", help="report dark/extreme literal colors lacking a body.theme-light override")
    parser.add_argument("--i18n-untranslated-report", action="store_true", help="print every non-allowlisted locale value that still equals en.json")
    args = parser.parse_args(argv)

    assets = args.assets or sorted(ASSETS)
    if args.lint_light:
        violations = lint_light_mode_pairs()
        for line in violations:
            print(line, file=sys.stderr)
        print(f"{len(violations)} light-mode pairing issue(s)", file=sys.stderr)
        return 1 if violations else 0
    if args.i18n_untranslated_report:
        warnings, errors = i18n_untranslated_report(sample_limit=None)
        for line in warnings:
            print(line)
        for line in errors:
            print(line, file=sys.stderr)
        return 1 if errors else 0
    if args.watch:
        return watch_loop(assets)
    try:
        if args.check:
            check_css_braces()
            stale = [asset for asset in assets if not check_asset(asset)]
            stale += check_locales()
            # the documented pre-commit gate runs the FULL lint set — duplicate top-level
            # declarations, undefined CSS vars, AND theme-light pairing (the last previously ran only under
            # --lint-light, so it was absent from --check / the CPS check list).
            lint_errors = (
                lint_duplicate_functions()
                + lint_direct_button_construction()
                + lint_shared_ui_ownership()
                + lint_normalized_production_clones()
                + lint_source_control_characters()
                + lint_asset_source_completeness()
                + lint_css_structure()
                + lint_identical_theme_restatements()
                + lint_repeated_semantic_declaration_sets()
                + lint_code_syntax_color_ownership()
                + lint_raw_standard_border_radii()
                + lint_raw_standard_motion_durations()
                + lint_raw_standard_font_sizes()
                + lint_raw_standard_spacing()
                + lint_undefined_css_vars()
                + lint_unused_css_tokens()
                + lint_raw_literal_equals_token()
                + lint_novel_component_colors()
                + lint_repeated_raw_component_literals()
                + lint_repeated_raw_box_shadows()
                + lint_unowned_z_indexes()
                + lint_raw_window_viewport_reads()
                + lint_light_mode_pairs()
                + lint_semantic_color_contrast()
            )
            i18n_warnings, i18n_errors = i18n_untranslated_report()
            for warning in i18n_warnings:
                print(warning, file=sys.stderr)
            lint_errors += i18n_errors
            for err in lint_errors:
                print(err, file=sys.stderr)
            if stale or lint_errors:
                if stale:
                    print("stale static assets: " + ", ".join(stale), file=sys.stderr)
                return 1
            return 0

        # a plain build goes through build_once (the same path --watch uses), so an invariant
        # added there (e.g. check_css_braces) can never be skipped by the non-watch build.
        changed = build_once(assets)
        if changed:
            print("rebuilt static assets: " + ", ".join(changed))
        return 0
    except BuildError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
