import pytest

from tools.static_build import ASSETS
from tools.static_build import BuildError
from tools.static_build import build_asset
from tools.static_build import build_pseudo_catalog
from tools.static_build import check_css_braces
from tools.static_build import i18n_untranslated_entries
from tools.static_build import i18n_untranslated_report
from tools.static_build import i18n_literal_key_errors
from tools.static_build import i18n_duplicate_ownership_errors
from tools.static_build import i18n_visible_literal_sink_errors
from tools.static_build import locale_expected_keys
from tools.static_build import locale_registry_errors
from tools.static_build import locale_semantic_errors
from tools.static_build import locale_key_errors
from tools.static_build import plural_family_bases
from tools.static_build import source_catalogs
from tools.static_build import lint_duplicate_functions
from tools.static_build import lint_repeated_raw_component_literals
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


def test_locale_registry_matches_source_catalog_stems():
    assert locale_registry_errors(source_catalogs()) == []
    assert locale_registry_errors({"en": {}, "made-up": {}}) == [
        "missing registered locale catalogs: ar, de, es, fr, he, hi, it, ja, ko, nl, pl, pt-BR, ru, th, tr, vi, zh-Hans, zh-Hant",
        "unregistered source locale catalogs: made-up",
    ]


def test_locale_key_parity_requires_exact_locale_plural_categories():
    source = {"item.one": "{count} item", "item.other": "{count} items"}
    missing_many = {"en": source, "fr": {"item.one": "{count} article", "item.other": "{count} articles"}}
    assert locale_key_errors(missing_many) == ["fr.json missing keys: item.many"]

    exact = {**missing_many["fr"], "item.many": "{count} articles"}
    assert locale_key_errors({"en": source, "fr": exact}) == []
    unexpected = {**exact, "item.few": "{count} articles"}
    assert locale_key_errors({"en": source, "fr": unexpected}) == ["fr.json has unknown keys: item.few"]


def test_shipped_catalogs_have_all_598_locale_specific_plural_forms():
    catalogs = source_catalogs()
    source = catalogs["en"]
    assert len(plural_family_bases(source)) == 46
    extras = 0
    for locale, catalog in catalogs.items():
        if locale == "en":
            continue
        expected = locale_expected_keys(source, locale)
        assert set(catalog) == expected
        extras += len(expected) - len(source)
    assert extras == 598


def test_i18n_literal_key_errors_checks_exact_and_plural_calls_but_skips_dynamic_prefixes(tmp_path):
    js_source = tmp_path / "surface.js"
    js_source.write_text(
        "t('present.key');\n"
        "localizedHtml('missing.key');\n"
        "tPlural('count.files', count);\n"
        "t('dynamic.prefix.' + suffix);\n",
        encoding="utf-8",
    )
    python_source = tmp_path / "surface.py"
    python_source.write_text(
        "server_string(locale, 'present.server')\n"
        "server_string(\n"
        "    locale,\n"
        "    'missing.server',\n"
        ")\n"
        "yoagent_text(locale, 'missing.yoagent')\n"
        "user_message_payload('missing.payload', 'raw diagnostic')\n"
        "message_descriptor('missing.descriptor', 'raw diagnostic')\n"
        "message_fields('message', 'missing.fields', 'raw diagnostic')\n"
        "RequestValidationError('raw diagnostic', 'missing.validation')\n"
        "worker.update_last_action('missing.action', 'raw diagnostic')\n"
        "log_event(None, 'test', 'raw diagnostic', message_key='missing.event')\n"
        "server_string(locale, dynamic_key)\n"
        "server_plural(locale, 'missing.plural', count)\n"
        "SURFACE_I18N_KEY_MAP = {'status': 'missing.map'}\n",
        encoding="utf-8",
    )
    catalog = {
        "present.key": "Present",
        "present.server": "Present on the server",
        "count.files.one": "{count} file",
    }
    errors = i18n_literal_key_errors(catalog, [js_source, python_source])
    assert len(errors) == 12
    assert "surface.js:2 missing i18n key(s): missing.key" in errors[0]
    assert "surface.js:3 missing i18n key(s): count.files.other" in errors[1]
    assert "surface.py:2 missing i18n key(s): missing.server" in errors[2]
    assert "surface.py:6 missing i18n key(s): missing.yoagent" in errors[3]
    assert "surface.py:7 missing i18n key(s): missing.payload" in errors[4]
    assert "surface.py:8 missing i18n key(s): missing.descriptor" in errors[5]
    assert "surface.py:9 missing i18n key(s): missing.fields" in errors[6]
    assert "surface.py:10 missing i18n key(s): missing.validation" in errors[7]
    assert "surface.py:11 missing i18n key(s): missing.action" in errors[8]
    assert "surface.py:12 missing i18n key(s): missing.event" in errors[9]
    assert "surface.py:14 missing i18n key(s): missing.plural.one, missing.plural.other" in errors[10]
    assert "surface.py:15 missing i18n key(s): missing.map" in errors[11]


def test_i18n_visible_literal_sink_errors_rejects_visible_prose_but_allows_units(tmp_path):
    js_source = tmp_path / "surface.js"
    js_source.write_text(
        "node.textContent = 'Raw visible status';\n"
        "node.title = t('present.key');\n"
        "node.setAttribute('aria-label', 'Raw accessible label');\n"
        "statusErr('Raw toast error');\n"
        "latency.textContent = '-- ms';\n"
        "node.textContent = `Raw template ${name}`;\n"
        "node.innerHTML = `<span>${action.label}</span>`;\n",
        encoding="utf-8",
    )
    css_source = tmp_path / "surface.css"
    css_source.write_text(
        ".raw::before { content: \"Raw CSS label\"; }\n"
        ".localized::before { content: attr(data-label); }\n"
        ".glyph::before { content: \"Aa\"; }\n",
        encoding="utf-8",
    )

    assert i18n_visible_literal_sink_errors([js_source, css_source]) == [
        "surface.css:1 visible literal bypasses i18n: 'Raw CSS label'",
        "surface.js:1 visible literal bypasses i18n: 'Raw visible status'",
        "surface.js:3 visible literal bypasses i18n: 'Raw accessible label'",
        "surface.js:4 visible literal bypasses i18n: 'Raw toast error'",
        "surface.js:6 visible literal bypasses i18n: 'Raw template ${name}'",
        "surface.js:7 raw user-visible field bypasses i18n: 'action.label'",
    ]


def test_i18n_visible_literal_sink_tree_is_clean():
    assert i18n_visible_literal_sink_errors() == []


def test_pseudo_value_accents_text_keeps_tokens_and_pads():
    protected = "`git status` https://example.com/a /static/yolomux.js ~/repo/file Ctrl-Alt-C <strong>safe</strong>"
    out = pseudo_value(f"Save {{name}} as {{date:%Y%m%d}}-{{seq:03d}} with {protected}")
    assert out.startswith("⟦") and out.endswith("⟧")
    assert "{name}" in out          # interpolation tokens are preserved verbatim
    assert "{date:%Y%m%d}" in out and "{seq:03d}" in out  # documented format tokens are also syntax
    for token in ("`git status`", "https://example.com/a", "/static/yolomux.js", "~/repo/file", "Ctrl-Alt-C", "<strong>", "</strong>"):
        assert token in out
    assert "Save" not in out        # the rest is accented
    assert "·" in out               # padded to surface overflow


def test_build_pseudo_catalog_covers_every_source_key():
    source = {"x": "Hello", "y": "World {n}"}
    pseudo = build_pseudo_catalog(source)
    assert set(pseudo) == set(source)
    assert "{n}" in pseudo["y"]


def test_i18n_untranslated_report_lists_real_matches_and_allows_brand_strings():
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
    warnings, errors = i18n_untranslated_report(catalogs, sample_limit=None)
    assert warnings == ["WARNING: i18n untranslated values in zh-Hant.json: 1; keys: pref.uploads.custom_actions.label"]
    assert errors == ["zh-Hant.json has 1 unintended English fallback value(s)"]


def test_i18n_untranslated_report_fails_every_unintended_fallback():
    catalogs = {"en": {"a": "Alpha", "b": "Beta"}, "fr": {"a": "Alpha", "b": "Beta"}}
    warnings, errors = i18n_untranslated_report(catalogs, sample_limit=1)
    assert warnings == ["WARNING: i18n untranslated values in fr.json: 2; keys: a (+1 more)"]
    assert errors == ["fr.json has 2 unintended English fallback value(s)"]


def test_i18n_untranslated_report_does_not_hide_graph_labels_by_locale():
    catalogs = {
        "en": {"common.clientLatency": "Client latency"},
        "fr": {"common.clientLatency": "Client latency"},
        "zh-Hant": {"common.clientLatency": "Client latency"},
    }
    entries = i18n_untranslated_entries(catalogs)
    assert entries["fr"] == ["common.clientLatency"]
    assert entries["zh-Hant"] == ["common.clientLatency"]


def test_locale_semantic_errors_enforces_placeholders_and_protected_syntax():
    catalogs = {
        "en": {
            "a": "Open {count} at `git show HEAD` via https://example.test/x",
            "b": "Done",
        },
        "fr": {
            "a": "Ouvrir {total} avec `git show main` via https://example.test/y",
            "b": "Terminé",
        },
    }
    errors = [error for error in locale_semantic_errors(catalogs) if not error.startswith("missing registered locale catalogs:")]
    assert any("fr.json placeholder drift at a" in error for error in errors)
    assert any("fr.json protected-token drift at a" in error for error in errors)


def test_i18n_duplicate_ownership_rejects_parallel_concepts_but_allows_reviewed_contexts_and_plurals():
    source = {
        "feature.cancel": "Cancel",
        "dialog.cancel": "Cancel",
        "item.one": "{count} item",
        "item.other": "{count} item",
        "toast.keep": "Keep",
        "update.dismiss": "Keep",
    }
    assert i18n_duplicate_ownership_errors(source) == [
        "en.json duplicate locale concept lacks a shared owner: dialog.cancel, feature.cancel = 'Cancel'",
    ]


def test_i18n_duplicate_ownership_rejects_parallel_plural_families_but_allows_reviewed_contexts():
    duplicate = {
        "first.one": "{count} item",
        "first.other": "{count} items",
        "second.one": "{count} item",
        "second.other": "{count} items",
    }
    assert i18n_duplicate_ownership_errors(duplicate) == [
        "en.json duplicate plural locale concept lacks a shared owner: first, second = ('{count} item', '{count} items')",
    ]
    reviewed = {
        "relative.compact.day.one": "{count} day ago",
        "relative.compact.day.other": "{count} days ago",
        "summary.relative.day.one": "{count} day ago",
        "summary.relative.day.other": "{count} days ago",
    }
    assert i18n_duplicate_ownership_errors(reviewed) == []


def test_shipped_catalog_has_no_unowned_duplicate_locale_concepts():
    assert i18n_duplicate_ownership_errors(source_catalogs()["en"]) == []


def test_locale_semantic_errors_validate_locale_only_plural_forms_against_other():
    catalogs = {
        "en": {"item.one": "Open {count} item", "item.other": "Open {count} items via `git status`"},
        "fr": {
            "item.one": "Ouvrir {count} élément",
            "item.other": "Ouvrir {count} éléments via `git status`",
            "item.many": "Ouvrir {total} éléments via `git diff`",
        },
    }
    errors = locale_semantic_errors(catalogs)
    assert any("fr.json placeholder drift at item.many" in error for error in errors)
    assert any("fr.json protected-token drift at item.many" in error for error in errors)


def test_locale_semantic_errors_protects_documented_format_tokens_without_interpolating_them():
    catalogs = {
        "en": {"template": "Use {date:%Y%m%d}, {seq:03d}, and {name}."},
        "fr": {"template": "Utiliser {date:%d%m%Y}, {seq:03d} et {name}."},
    }
    errors = locale_semantic_errors(catalogs)
    assert not any("placeholder drift" in error for error in errors)
    assert any("fr.json protected-token drift at template" in error for error in errors)


def test_locale_semantic_errors_protects_absolute_asset_paths_without_matching_html_tags():
    catalogs = {
        "en": {"asset": "Load /static/xterm.js before </main>."},
        "fr": {"asset": "Charger /static/terminal.js avant </main>."},
    }
    errors = [error for error in locale_semantic_errors(catalogs) if not error.startswith("missing registered locale catalogs:")]
    assert errors == [
        "fr.json protected-token drift at asset: expected {'/static/xterm.js': 1, '</main>': 1}, got {'/static/terminal.js': 1, '</main>': 1}"
    ]


def test_locale_semantic_errors_rejects_blank_translation_for_nonblank_source():
    errors = [
        error
        for error in locale_semantic_errors({"en": {"a": "Visible text"}, "fr": {"a": ""}})
        if not error.startswith("missing registered locale catalogs:")
    ]
    assert errors == ["fr.json blank translation at a"]


def test_locale_semantic_errors_requires_chinese_yo_brand_marker():
    catalogs = {
        "en": {"brand": "Open YO!stats"},
        "zh-Hans": {"brand": "打开 AI 统计"},
        "zh-Hant": {"brand": "開啟 YO!統計"},
    }
    errors = locale_semantic_errors(catalogs)
    assert "zh-Hans.json must localize YO as 优 at brand" in errors
    assert "zh-Hant.json must localize YO as 優 at brand" in errors


def test_locale_semantic_errors_rejects_prose_under_former_broad_exemptions():
    catalogs = {
        "en": {
            "pref.yoagent.claude_model.fable": "Fable 5 (most capable)",
            "state.short.blocked": "Blocked",
        },
        "fr": {
            "pref.yoagent.claude_model.fable": "Fable 5 (most capable)",
            "state.short.blocked": "Blocked",
        },
    }
    entries = i18n_untranslated_entries(catalogs)
    assert entries["fr"] == ["pref.yoagent.claude_model.fable", "state.short.blocked"]


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


def test_repeated_raw_component_literal_lint_tree_is_clean():
    assert lint_repeated_raw_component_literals() == []


def test_repeated_raw_component_literal_lint_flags_new_repeats(monkeypatch, tmp_path):
    import tools.static_build as sb
    bad = tmp_path / "bad.css"
    bad.write_text(".a { color: #123456; }\n.b { border-color: #123456; }\n.c { color: #abcdef; }\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["bad.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_repeated_raw_component_literals() == [
        "raw component color #123456 repeats in bad.css:1, bad.css:2; move it to a CSS token or add a reviewed allowlist reason"
    ]


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
