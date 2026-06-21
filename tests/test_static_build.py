import pytest

from tools.static_build import ASSETS
from tools.static_build import BuildError
from tools.static_build import build_asset
from tools.static_build import build_pseudo_catalog
from tools.static_build import check_css_braces
from tools.static_build import i18n_untranslated_entries
from tools.static_build import i18n_untranslated_report
from tools.static_build import locale_key_errors
from tools.static_build import lint_duplicate_functions
from tools.static_build import lint_raw_window_viewport_reads
from tools.static_build import pseudo_value
from tools.static_build import repo_path
from tools.static_build import _color_luminance_alpha
from tools.static_build import _first_color_literal
from tools.static_build import _light_covers
from tools.static_build import lint_light_mode_pairs


def test_generated_static_assets_are_current():
    for asset in ASSETS:
        assert (repo_path("static") / asset).read_text(encoding="utf-8") == build_asset(asset)


def test_i18n_runtime_partial_is_registered_before_core():
    js = ASSETS["yolomux.js"]
    assert "static_src/js/yolomux/05_i18n.js" in js
    assert js.index("static_src/js/yolomux/05_i18n.js") < js.index("static_src/js/yolomux/10_core_utils.js")


def test_locale_key_parity_flags_missing_and_extra():
    catalogs = {
        "en": {"a": "A", "b": "B"},
        "fr": {"a": "Aa"},               # missing b
        "de": {"a": "Aa", "b": "Bb", "c": "Cc"},  # extra c
    }
    errors = locale_key_errors(catalogs)
    assert any("fr.json missing keys: b" in e for e in errors)
    assert any("de.json has unknown keys: c" in e for e in errors)
    # A perfectly-matching catalog set has no errors.
    assert locale_key_errors({"en": {"a": "A"}, "fr": {"a": "Aa"}}) == []


def test_pseudo_value_accents_text_keeps_tokens_and_pads():
    out = pseudo_value("Save {name}")
    assert out.startswith("⟦") and out.endswith("⟧")
    assert "{name}" in out          # interpolation tokens are preserved verbatim
    assert "Save" not in out        # the rest is accented
    assert "·" in out               # padded to surface overflow


def test_build_pseudo_catalog_covers_every_source_key():
    source = {"x": "Hello", "y": "World {n}"}
    pseudo = build_pseudo_catalog(source)
    assert set(pseudo) == set(source)
    assert "{n}" in pseudo["y"]


def test_i18n_untranslated_report_lists_real_matches_and_allows_brand_strings(monkeypatch):
    import tools.static_build as sb
    monkeypatch.setattr(sb, "I18N_UNTRANSLATED_BASELINE", {})
    catalogs = {
        "en": {
            "about.github": "YOLOmux GitHub",
            "common.ok": "OK",
            "pref.uploads.custom_actions.label": "Custom file-drop actions",
        },
        "zh-Hant": {
            "about.github": "YOLOmux GitHub",
            "common.ok": "OK",
            "pref.uploads.custom_actions.label": "Custom file-drop actions",
        },
    }
    entries = i18n_untranslated_entries(catalogs)
    assert entries["zh-Hant"] == ["pref.uploads.custom_actions.label"]
    warnings, errors = sb.i18n_untranslated_report(catalogs, sample_limit=None)
    assert warnings == ["WARNING: i18n untranslated values in zh-Hant.json: 1; keys: pref.uploads.custom_actions.label"]
    assert errors == []


def test_i18n_untranslated_report_uses_baseline_for_regression(monkeypatch):
    import tools.static_build as sb
    catalogs = {"en": {"a": "Alpha", "b": "Beta"}, "fr": {"a": "Alpha", "b": "Beta"}}
    monkeypatch.setattr(sb, "I18N_UNTRANSLATED_BASELINE", {"fr": 1})
    warnings, errors = sb.i18n_untranslated_report(catalogs, sample_limit=1)
    assert warnings == ["WARNING: i18n untranslated values in fr.json: 2; keys: a (+1 more)"]
    assert errors == ["fr.json untranslated-value count regressed: 2 > baseline 1"]


def test_i18n_untranslated_report_cited_zh_key_before_and_after_backfill():
    before = {
        "en": {
            "pref.uploads.custom_actions.label": "Custom file-drop actions",
            "pref.uploads.custom_actions.help": "Custom actions, one per line: Label | prompt text or shell:command | optional categories. Template fields: {path}, {qpath}, {paths}, {qpaths}, {name}, {count}, {category}.",
        },
        "zh-Hant": {
            "pref.uploads.custom_actions.label": "Custom file-drop actions",
            "pref.uploads.custom_actions.help": "Custom actions, one per line: Label | prompt text or shell:command | optional categories. Template fields: {path}, {qpath}, {paths}, {qpaths}, {name}, {count}, {category}.",
        },
    }
    assert i18n_untranslated_entries(before)["zh-Hant"] == [
        "pref.uploads.custom_actions.help",
        "pref.uploads.custom_actions.label",
    ]
    import tools.static_build as sb
    current = sb.source_catalogs()
    current_zh = i18n_untranslated_entries(current)["zh-Hant"]
    assert "pref.uploads.custom_actions.help" not in current_zh
    assert "pref.uploads.custom_actions.label" not in current_zh


def test_css_braces_are_balanced_in_every_partial():
    # The shipped CSS partials must each be brace-balanced (a truncated rule once split across
    # two partials and only rebalanced by accident in the bundle). This passes today; it guards regressions.
    check_css_braces()


def test_duplicate_function_lint_tree_is_clean():
    # K1: duplicate top-level function declarations silently shadow when partials are concatenated.
    assert lint_duplicate_functions() == []


def test_duplicate_function_lint_flags_cross_file_duplicates(monkeypatch, tmp_path):
    import tools.static_build as sb
    first = tmp_path / "first.js"
    second = tmp_path / "second.js"
    first.write_text("function shared() {}\nfunction uniqueOne() {}\n", encoding="utf-8")
    second.write_text("  function indentedIsIgnored() {}\nfunction shared() {}\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.js", ["first.js", "second.js"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_duplicate_functions() == ["duplicate top-level declaration 'shared' in: first.js, second.js"]


def test_duplicate_declaration_lint_also_catches_top_level_const(monkeypatch, tmp_path):
    # a duplicate top-level const across concatenated partials silently shadows too.
    import tools.static_build as sb
    first = tmp_path / "first.js"
    second = tmp_path / "second.js"
    first.write_text("const DUP = 1;\nfunction onlyHere() {}\n", encoding="utf-8")
    second.write_text("const DUP = 2;\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.js", ["first.js", "second.js"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_duplicate_functions() == ["duplicate top-level declaration 'DUP' in: first.js, second.js"]


def test_undefined_css_var_lint_is_clean():
    # every var(--x) in the bundle resolves to a CSS def or a JS setProperty/inline-style.
    from tools.static_build import lint_undefined_css_vars
    assert lint_undefined_css_vars() == []


def test_raw_window_viewport_lint_tree_is_clean():
    assert lint_raw_window_viewport_reads() == []


def test_raw_window_viewport_lint_flags_unowned_reads(monkeypatch, tmp_path):
    import tools.static_build as sb
    bad = tmp_path / "bad.js"
    good = tmp_path / "good.js"
    bad.write_text("const width = window.innerWidth;\n", encoding="utf-8")
    good.write_text("const height = window.innerHeight; // static-build-allow-window-viewport\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.js", ["bad.js", "good.js"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_raw_window_viewport_reads() == [
        "bad.js:1: raw window.innerWidth/innerHeight read; use appViewport()"
    ]


def test_check_css_braces_flags_an_unbalanced_partial(monkeypatch, tmp_path):
    import tools.static_build as sb
    bad = tmp_path / "bad.css"
    bad.write_text(".a { color: red; } .b {\n", encoding="utf-8")  # truncated rule, missing }
    monkeypatch.setitem(sb.ASSETS, "probe.css", ["__bad__.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: bad if p == "__bad__.css" else sb.REPO_ROOT / p)
    with pytest.raises(BuildError, match="unbalanced CSS braces"):
        sb.check_css_braces()


def test_expected_locale_outputs_raises_on_parity_failure(monkeypatch):
    import tools.static_build as sb
    monkeypatch.setattr(sb, "source_catalogs", lambda: {"en": {"a": "A"}, "fr": {}})
    with pytest.raises(BuildError):
        sb.expected_locale_outputs()


def test_light_mode_lint_helpers_classify_colors():
    # Option-2 lint helpers: extreme-vs-mid luminance + literal extraction (ignoring themed values).
    dark_lum, dark_a = _color_luminance_alpha("#11151d")
    light_lum, light_a = _color_luminance_alpha("#f2f5f8")
    mid_lum, _ = _color_luminance_alpha("#76b900")
    assert dark_lum < 0.18 and dark_a == 1.0          # a dark background literal
    assert light_lum > 0.82                            # a near-white text literal
    assert 0.18 <= mid_lum <= 0.82                     # a vibrant brand color is not flagged
    assert _color_luminance_alpha("rgba(8, 31, 8, 0.4)")[1] == pytest.approx(0.4)
    # themed/adaptive values yield no literal (so they are never flagged)
    assert _first_color_literal("var(--panel)") is None
    assert _first_color_literal("color-mix(in srgb, var(--x) 50%, transparent)") is None
    assert _first_color_literal("#0b0e14") == "#0b0e14"


def test_light_mode_lint_contextual_pairing():
    # Durable Fix B: contextual (specificity-aware) pairing — a dark rule is covered by a body.theme-light
    # override on the SAME selector OR a more-specific descendant context, not just the exact selector.
    assert _light_covers(".session-button-name", [".pane-tab:not(.active) .session-button-name"]) is True
    assert _light_covers(".markdown-body pre", [".yoagent-chat .markdown-body pre"]) is True
    assert _light_covers(".foo", [".foo"]) is True
    # not covered: an unrelated selector, or a mere substring that is not a descendant-suffix.
    assert _light_covers(".foo", [".bar"]) is False
    assert _light_covers(".button-name", [".session-button-name"]) is False


def test_light_mode_lint_tree_is_clean():
    # Durable Fix B: the shipped CSS has no dark/extreme literal lacking a body.theme-light override
    # (exact or contextual), except the vetted LIGHT_LINT_ALLOWLIST. A NEW unpaired dark-box /
    # invisible-text rule fails here — review it, then fix it (add the light override) or allowlist it
    # with a reason. This is the enforceable "light-mode coverage is an invariant" gate.
    violations = lint_light_mode_pairs()
    assert violations == [], "light-mode pairing regressions:\n  " + "\n  ".join(violations)
