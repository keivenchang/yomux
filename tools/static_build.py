# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Build served YOLOmux static assets from ordered source partials."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import json
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
RAW_TOKEN_LITERAL_IGNORED_VALUES = {"#ffffff"}
RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST: dict[str, str] = {
    "#ffffff": "shared white paint in fixed light documents, text, and light-theme mixes",
    "#ffe7a3": "paired YO!agent tool-call warning colors",
    "#ffe27a": "paired warning/status accent colors",
    "#ffd8dc": "paired danger text colors",
    "#ff9f1c": "paired warning/orange component colors",
    "#f7d2d8": "paired disconnected-share colors",
    "#f4f7fb": "paired light tree surface colors",
    "#ef4444": "paired about-brand / error accent colors",
    "#eef6ff": "paired YO!agent light action-code surfaces",
    "#eef1f5": "paired muted editor surface colors",
    "#e8f0fb": "paired info-card surface colors",
    "#e7ebf1": "paired muted popover/button text colors",
    "#dfe7f2": "paired topbar popover text colors",
    "#d81f32": "paired diff/compare removed colors",
    "#c8941e": "paired warning borders",
    "#b98c24": "paired file-hover warning borders",
    "#aab4c4": "paired muted popover border colors",
    "#9fb0c4": "paired preferences preview muted text colors",
    "#8ff2a7": "paired green status sample colors",
    "#8c959f": "paired markdown quote border colors",
    "#8ab4f8": "paired YO!agent action-code borders",
    "#7c2d12": "paired conflict prompt warning colors",
    "#7a2e3d": "paired disconnected-share dark text colors",
    "#586072": "paired muted tree/popover text colors",
    "#3b0a0a": "paired conflict compare removed text colors",
    "#3a2b00": "paired warning dark text colors",
    "#2b3242": "paired status label dark surface colors",
    "#2a1e00": "paired warning dark surface colors",
    "#273140": "paired preferences/topbar dark preview colors",
    "#263044": "paired tree/popover dark surface colors",
    "#166534": "paired light success text colors",
    "#161d29": "paired popover dark surface colors",
    "#14171d": "paired file-tree warning dark surface colors",
    "#10151d": "paired topbar dark surface colors",
    "#0f4c81": "paired YO!agent action heading colors",
    "#0b1017": "paired preferences dark preview colors",
    "#0645ad": "paired editor vanilla link colors",
    "#051408": "paired green status dark surface colors",
    "#00a152": "paired diff/compare added colors",
}
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
    "fr": frozenset({"transcript.role.message"}),
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
        "static_src/js/yolomux/80_info_panel.js",
        "static_src/js/yolomux/81_yoagent_panel.js",
        "static_src/js/yolomux/82_preferences_panel.js",
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


def _color_luminance_alpha(color: str) -> tuple[float, float] | None:
    """(relative luminance 0..1, alpha 0..1) for a #hex / rgb()/rgba() literal, else None."""
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


def _token_hex_values() -> dict[str, list[str]]:
    token_file = repo_path("static_src/css/yolomux/00_tokens_base.css")
    css = re.sub(r"/\*.*?\*/", "", read_text(token_file), flags=re.DOTALL)
    values: dict[str, list[str]] = defaultdict(list)
    for match in re.finditer(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", css):
        values[match.group(2).lower()].append(match.group(1))
    return values


def lint_raw_literal_equals_token() -> list[str]:
    """Semantic token colors should be referenced through `var(--token)`, not copied as raw hex.

    White and `var(--x, #fallback)` literals are intentionally left alone: white is used as real paint in
    shadows/surfaces, and fallback literals are part of the token reference rather than a parallel copy.
    """
    token_values = _token_hex_values()
    errors: list[str] = []
    for part in ASSETS.get("yolomux.css", []):
        if part.endswith("00_tokens_base.css"):
            continue
        path = repo_path(part)
        try:
            text = read_text(path)
        except FileNotFoundError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in re.finditer(r"#[0-9a-fA-F]{6}\b", line):
                literal = match.group(0).lower()
                if literal not in token_values:
                    continue
                if literal in RAW_TOKEN_LITERAL_IGNORED_VALUES:
                    continue
                if "var(" in line:
                    continue
                tokens = ", ".join(sorted(set(token_values[literal])))
                errors.append(f"{part}:{line_no}: raw color {literal} duplicates token value(s) {tokens}; use var(--token)")
    return errors


def lint_repeated_raw_component_literals() -> list[str]:
    """New repeated component hex colors must become tokens or get a reviewed allowlist reason."""
    occurrences: dict[str, list[str]] = defaultdict(list)
    for part in ASSETS.get("yolomux.css", []):
        if part.endswith("00_tokens_base.css"):
            continue
        path = repo_path(part)
        try:
            text = read_text(path)
        except FileNotFoundError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "var(" in line:
                continue
            for match in re.finditer(r"#[0-9a-fA-F]{6}\b", line):
                literal = match.group(0).lower()
                occurrences[literal].append(f"{part}:{line_no}")
    errors: list[str] = []
    for literal, locations in sorted(occurrences.items()):
        if len(locations) < 2:
            continue
        if literal in RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST:
            continue
        shown = ", ".join(locations[:4])
        suffix = "" if len(locations) <= 4 else f" (+{len(locations) - 4} more)"
        errors.append(f"raw component color {literal} repeats in {shown}{suffix}; move it to a CSS token or add a reviewed allowlist reason")
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
                + lint_undefined_css_vars()
                + lint_raw_literal_equals_token()
                + lint_repeated_raw_component_literals()
                + lint_raw_window_viewport_reads()
                + lint_light_mode_pairs()
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
