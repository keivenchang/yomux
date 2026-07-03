import re

import pytest

from tools import static_build as sb
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
from tools.static_build import lint_identical_theme_restatements
from tools.static_build import lint_css_structure
from tools.static_build import lint_code_syntax_color_ownership
from tools.static_build import lint_raw_standard_border_radii
from tools.static_build import lint_raw_standard_font_sizes
from tools.static_build import lint_raw_standard_motion_durations
from tools.static_build import lint_repeated_raw_box_shadows
from tools.static_build import lint_repeated_raw_component_literals
from tools.static_build import lint_repeated_semantic_declaration_sets
from tools.static_build import lint_source_control_characters
from tools.static_build import lint_unowned_z_indexes
from tools.static_build import lint_undefined_css_vars
from tools.static_build import lint_raw_window_viewport_reads
from tools.static_build import lint_raw_literal_equals_token
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
    first = tmp_path / "first.js"
    second = tmp_path / "second.js"
    first.write_text("function shared() {}\nfunction uniqueOne() {}\n", encoding="utf-8")
    second.write_text("  function indentedIsIgnored() {}\nfunction shared() {}\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.js", ["first.js", "second.js"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_duplicate_functions() == ["duplicate top-level declaration 'shared' in: first.js, second.js"]


def test_duplicate_declaration_lint_also_catches_top_level_const(monkeypatch, tmp_path):
    # a duplicate top-level const across concatenated partials silently shadows too.
    first = tmp_path / "first.js"
    second = tmp_path / "second.js"
    first.write_text("const DUP = 1;\nfunction onlyHere() {}\n", encoding="utf-8")
    second.write_text("const DUP = 2;\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.js", ["first.js", "second.js"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_duplicate_functions() == ["duplicate top-level declaration 'DUP' in: first.js, second.js"]


def test_source_control_character_lint_tree_is_clean():
    assert lint_source_control_characters() == []


def test_source_control_character_lint_rejects_binary_bytes(monkeypatch, tmp_path):
    source = tmp_path / "first.js"
    source.write_bytes(b"ok\nbad\x00x\x01\n")
    monkeypatch.setitem(sb.ASSETS, "yolomux.js", ["first.js"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_source_control_characters() == [
        "first.js:2:4: source control character U+0000; use a textual escape or structured signature",
        "first.js:2:6: source control character U+0001; use a textual escape or structured signature",
    ]


def test_css_structure_lint_tree_is_clean():
    assert lint_css_structure() == []


def test_css_structure_lint_flags_duplicate_property_selector_and_empty_rule(monkeypatch, tmp_path):
    first = tmp_path / "first.css"
    second = tmp_path / "second.css"
    first.write_text(
        ":root { --tone: red; --tone: blue; }\n"
        ".empty { /* migration remnant */ }\n"
        ".shared { color: red; }\n"
        "@media (width < 10px) { .shared { color: blue; } }\n",
        encoding="utf-8",
    )
    second.write_text(".shared { background: black; }\n6\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["first.css", "second.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_css_structure() == [
        "first.css:1: CSS rule ':root' declares '--tone' more than once",
        "first.css:2: empty CSS rule '.empty'",
        "second.css:2: orphan CSS text after final rule: '6'",
        "duplicate CSS selector '.shared' in top level: first.css:3, second.css:1; merge it into one owner",
    ]


def test_identical_theme_restatement_lint_tree_is_clean():
    assert lint_identical_theme_restatements() == []


def test_identical_theme_restatement_lint_catches_separate_and_grouped_copies(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    component.write_text(
        ".control { color: var(--text); background: black; }\n"
        "body.theme-light .control { color: var(--text); background: white; }\n"
        ".state, body.editor-theme-light .state { border-color: var(--line); }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_identical_theme_restatements() == [
        "component.css:2: theme selector 'body.theme-light .control' restates 'color: var(--text)' from component.css:1 base selector '.control'; remove the copy or lower fallback specificity",
        "component.css:3: theme selector 'body.editor-theme-light .state' restates 'border-color: var(--line)' from component.css:3 base selector '.state'; remove the copy or lower fallback specificity",
    ]


def test_repeated_semantic_declaration_set_lint_tree_is_clean():
    assert lint_repeated_semantic_declaration_sets() == []


def test_tokenized_component_base_rules_have_no_identical_light_restatements():
    tokens_css = repo_path("static_src/css/yolomux/00_tokens_base.css").read_text(encoding="utf-8")
    topbar_css = repo_path("static_src/css/yolomux/10_topbar_menus.css").read_text(encoding="utf-8")
    popovers_css = repo_path("static_src/css/yolomux/20_sessions_popovers.css").read_text(encoding="utf-8")
    preferences_css = repo_path("static_src/css/yolomux/30_preferences_changes.css").read_text(encoding="utf-8")
    layout_css = repo_path("static_src/css/yolomux/40_layout_panes_tabs.css").read_text(encoding="utf-8")
    file_tree_css = repo_path("static_src/css/yolomux/50_terminal_file_tree.css").read_text(encoding="utf-8")
    panels_css = repo_path("static_src/css/yolomux/60_editor_file_panels.css").read_text(encoding="utf-8")

    assert not re.search(r"body\.theme-light \.topbar\s*\{\s*background:\s*var\(--panel2\)", topbar_css)
    assert not re.search(r"body\.theme-light \.topbar:hover,\s*body\.theme-light \.topbar:focus-within\s*\{\s*background:\s*var\(--pane-tab-strip-bg\)", topbar_css)
    assert not re.search(r"body\.theme-light \.command-palette-view-chip\s*\{[^}]*var\(--active-control-soft-bg\)", preferences_css)
    assert not re.search(r"body\.theme-light \.preferences-inline-action\s*\{\s*color:\s*var\(--active-control-bg\)", preferences_css)
    assert "body.theme-light :is(.changes-toolbar, .changes-repo-group, .file-explorer, .file-explorer-tree-col)" in tokens_css
    assert ":where(body.theme-light) .changes-comparison-head" not in tokens_css
    assert ":where(body.theme-light) .changes-refresh," in preferences_css
    assert not re.search(r"body\.theme-light \.preferences-setting-control input\[type=\"number\"\],[\s\S]*?body\.theme-light \.diff-ref-suggestion-popover\s*\{[^}]*color:", preferences_css)
    assert "body.theme-light .ci-indicator:not(.branch-indicator):not(.pr-number-chip)" in popovers_css
    assert not re.search(r"body\.theme-light \.ci-indicator\.pr-number-chip\s*\{[^}]*(?:background|border-color):", popovers_css)
    assert "color: var(--pr-link-merged);" in popovers_css
    assert "color: var(--pr-link-merged-hover);" in popovers_css
    assert not re.search(r"body\.theme-light \.(?:meta|summary-context) a(?::hover)?\s*\{", layout_css)
    assert not re.search(r"\.pr-status-merged:hover", preferences_css)
    assert not re.search(r"body\.theme-light \.changes-repo-head\s*\{", preferences_css)
    assert not re.search(r"body\.theme-light \.changes-repo-title\s*\{", preferences_css)
    assert not re.search(r"body\.theme-light \.changes-diff-add-neutral\s*\{", preferences_css)
    assert not re.search(r"body\.theme-light \.keyboard-shortcuts-section h3\s*\{", preferences_css)
    assert not re.search(r"body\.theme-light :is\([^)]*\.changes-comparison-head", tokens_css)
    assert not re.search(r"body\.theme-light \.file-explorer-changes-panel \.changes-comparison-head\s*\{", panels_css)
    assert not re.search(r"body\.theme-light \.file-explorer-(?:changes-panel|date-reload-cluster) \.changes-refresh(?:,|\s*\{)", panels_css)
    assert not re.search(r"body\.theme-light \.yoagent-backend-pill-dot\s*\{[^}]*background:", file_tree_css)
    assert not re.search(r"body\.theme-light \.yoagent-composer-pill-backend[^\{]*\{[^}]*background:", file_tree_css)
    assert not re.search(r"body\.theme-light \.info-tree\s*\{[^}]*(?:--info-tree-ai-color:|--info-tree-path-color:|--info-tree-branch-color:|--info-tree-pr-color:)", file_tree_css)
    assert not re.search(r"body\.theme-light \.file-explorer-path(?:,|:focus)[^\{]*\{[^}]*color:\s*var\(--text\)", file_tree_css)
    assert not re.search(r"body\.theme-light \.file-tree-row\.current-directory:not\(\.selected\)\s*\{", file_tree_css)
    assert not re.search(r"body\.theme-light \.file-tree-row\.kind-dir(?:\.expanded|\[aria-expanded| >)[^\{]*\{[^}]*color:\s*var\(--disclosure-triangle-", file_tree_css)
    assert not re.search(r"body\.theme-light \.file-tree-row\.tabber-row\s*\{[^}]*(?:--tabber-path-color|--tabber-detail-color)", panels_css)
    assert not re.search(r"body\.theme-light \.yoagent-chat,\s*body\.theme-light \.yoagent-message\s*\{[^}]*(?:background|border-color):", file_tree_css)
    assert not re.search(r"body\.theme-light \.yoagent-message\.assistant\s*\{[^}]*border-color:", file_tree_css)
    assert not re.search(r"body\.theme-light \.yoagent-message\.assistant\.yoagent-agent-result\s*\{[^}]*border-inline-start-color:", file_tree_css)
    assert not re.search(r"body\.theme-light \.yoagent-message\.user\s*\{", file_tree_css)
    assert not re.search(r"body\.theme-light \.file-explorer-pane,\s*body\.theme-light \.file-explorer-tree-panel\s*\{[^}]*background:", panels_css)
    assert not re.search(r"body\.editor-theme-light \.file-editor-codemirror \.cm-search \.cm-dialog-close,[\s\S]*?body\.editor-theme-light \.file-editor-codemirror-panel \.cm-search \.cm-dialog-close\s*\{[^}]*background:\s*transparent", panels_css)


def test_compact_overflow_strips_have_one_shared_layout_owner():
    tokens_css = repo_path("static_src/css/yolomux/00_tokens_base.css").read_text(encoding="utf-8")
    topbar_css = repo_path("static_src/css/yolomux/10_topbar_menus.css").read_text(encoding="utf-8")
    file_tree_css = repo_path("static_src/css/yolomux/50_terminal_file_tree.css").read_text(encoding="utf-8")
    assert re.search(r"\.app-menu-bar,\s*\.file-explorer-quick-access,\s*\.file-explorer-quick-access-panel\s*\{[^}]*flex:\s*0 0 auto[^}]*overflow:\s*visible", tokens_css)
    assert not re.search(r"\.app-menu-bar\s*\{", topbar_css)
    assert not re.search(r"\.file-explorer-quick-access,\s*\.file-explorer-quick-access-panel\s*\{", file_tree_css)


def test_code_syntax_color_ownership_tree_is_clean():
    assert lint_code_syntax_color_ownership() == []


def test_code_syntax_color_ownership_requires_one_grouped_renderer_rule(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    component.write_text(
        ".markdown-body .code-string { color: var(--code-string) !important; }\n"
        ".cm-content .code-string { color: var(--code-string) !important; }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setattr(sb, "CODE_SYNTAX_COLOR_TOKENS", frozenset({"--code-string"}))
    monkeypatch.setattr(sb, "repo_path", lambda path: tmp_path / path)

    assert lint_code_syntax_color_ownership() == [
        "syntax color '--code-string' repeats in component.css:1, component.css:2; merge renderer selectors into one rule"
    ]

    component.write_text(
        ".markdown-body .code-string, .cm-content .code-string { color: var(--code-string) !important; }\n",
        encoding="utf-8",
    )
    assert lint_code_syntax_color_ownership() == []


@pytest.mark.parametrize(
    ("name", "declarations"),
    [
        ("active-control paint", ("color: var(--active-control-text)", "background: var(--active-control-bg)", "border-color: var(--active-control-border)")),
        ("disabled-control state", ("opacity: 0.42", "cursor: default")),
        ("inactive-agent-marker paint", ("color: var(--agent-inactive-marker-text)", "background: var(--agent-inactive-marker-bg)", "border-color: var(--agent-inactive-marker-border)")),
        ("pane pressed-control paint", ("color: var(--pane-ctl-pressed-fg, var(--pane-tab-active-text))", "background: var(--pane-ctl-pressed-bg, var(--pane-tab-active-bg))", "border-color: var(--pane-ctl-pressed-border, var(--pane-tab-active-border))")),
        ("YO!info text-action reset", ("padding: 0", "border: 0", "background: transparent", "text-align: left", "font: inherit", "cursor: pointer")),
        ("agent identity-cluster layout", ("display: inline-flex", "align-items: center", "gap: 2px", "flex: 0 0 auto", "vertical-align: middle")),
        ("editor toolbar hover paint", ("color: var(--editor-toolbar-control-hover-fg)", "border-color: var(--editor-toolbar-control-hover-border)", "background: var(--editor-toolbar-control-hover-bg)")),
        ("branch-indicator paint", ("color: var(--branch-indicator-text)", "background: var(--branch-indicator-bg)", "border-color: var(--branch-indicator-border)")),
        ("server-update reload rest paint", ("background: var(--danger-strong)", "color: var(--paint-white)")),
        ("server-update reload hover paint", ("border-color: var(--danger-strong-border)", "background: var(--danger-strong-hover)", "color: var(--paint-white)")),
        ("three-row content scaffold", ("height: 100%", "min-height: 0", "background: var(--bg)", "display: grid", "grid-template-rows: auto auto minmax(0, 1fr)")),
        ("two-row debug-view layout", ("display: grid", "grid-template-rows: auto minmax(0, 1fr)", "gap: 8px")),
        ("search-input focus ring", ("outline: 0", "border-color: var(--active-control-border)", "box-shadow: var(--active-control-focus-shadow)")),
        ("danger-status paint", ("color: var(--danger-text)", "background: var(--danger-bg)", "border-color: var(--danger-border)")),
        ("compact agent SVG geometry", ("flex-basis: 14px", "width: 14px", "height: 14px")),
        ("light panel-surface paint", ("color: var(--text)", "background: var(--panel)", "border-color: var(--line)")),
        ("single-line ellipsis", ("min-width: 0", "overflow: hidden", "text-overflow: ellipsis", "white-space: nowrap")),
        ("vanilla-preview code surface", ("color: var(--markdown-html-light-text)", "background: var(--lt-panel)", "border-color: var(--lt-line)")),
        ("inline-code paint", ("color: var(--code-inline)", "background: var(--code-inline-bg)", "border: 1px solid var(--code-inline-border)", "border-radius: var(--radius-sm)")),
        ("light code-block paint", ("color: var(--lt-code-block-text)", "background: var(--lt-code-block-bg)", "border-color: var(--lt-code-block-border)")),
        ("vanilla nested-code reset", ("color: inherit !important", "background: transparent !important", "border-color: transparent")),
        ("flexible tab text", ("flex: 1 1 auto", "min-width: 0", "max-width: none")),
        ("shared link rest paint", ("color: var(--link-soft)", "text-decoration: none")),
        ("shared link hover paint", ("color: var(--link-soft-hover)", "text-decoration: underline")),
        ("path-drag outline", ("outline: 2px dashed var(--pane-resizer-hover-bg)", "outline-offset: -5px")),
        ("file explorer chrome hover paint", ("color: var(--text)", "border-color: var(--text)")),
        ("YO!agent action hover paint", ("border-color: var(--active-control-focus-ring)", "color: var(--text)", "outline: 0")),
        ("topbar status surface shell", ("flex: 0 0 auto", "display: inline-flex", "align-items: center", "height: var(--compact-control-height)", "font-size: var(--ui-font-size-2xs)", "cursor: pointer", "white-space: nowrap")),
        ("compact overflow strip layout", ("flex: 0 0 auto", "min-width: 0", "display: inline-flex", "align-items: center", "gap: 1px", "overflow: visible")),
    ],
)
def test_repeated_semantic_declaration_set_lint_ignores_order_and_extra_properties(monkeypatch, tmp_path, name, declarations):
    first = tmp_path / "first.css"
    second = tmp_path / "second.css"
    first.write_text(
        f".first {{ {'; '.join(declarations)}; }}\n",
        encoding="utf-8",
    )
    second.write_text(
        f".second {{ {'; '.join(reversed(declarations))}; padding: 2px; }}\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["first.css", "second.css"])
    monkeypatch.setattr(sb, "repo_path", lambda path: tmp_path / path)
    assert lint_repeated_semantic_declaration_sets() == [
        f"semantic CSS declaration set {name!r} repeats in first.css:1, second.css:1; merge it into one grouped selector"
    ]


def test_standard_border_radius_lint_tree_is_clean():
    assert lint_raw_standard_border_radii() == []


def test_standard_border_radius_lint_rejects_single_compound_and_logical_copies(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    component.write_text(
        ".card { border-radius: 6px; }\n"
        ".segment { border-radius: 8px 0 0 8px; }\n"
        ".leaf { border-start-start-radius: 8px; }\n"
        ".custom { border-radius: 14px; }\n"
        ".multiline { border-radius:\n  4px; }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert lint_raw_standard_border_radii() == [
        "component.css:1: raw standard border radius 6px; use var(--radius-md)",
        "component.css:2: raw standard border radius 8px; use var(--radius-lg)",
        "component.css:3: raw standard border radius 8px; use var(--radius-lg)",
        "component.css:5: raw standard border radius 4px; use var(--radius-control)",
    ]


def test_standard_motion_duration_lint_tree_is_clean():
    assert lint_raw_standard_motion_durations() == []


def test_standard_motion_duration_lint_rejects_equivalent_component_cadences(monkeypatch, tmp_path):
    tokens = tmp_path / "00_tokens_base.css"
    component = tmp_path / "component.css"
    tokens.write_text(":root { --motion-interaction-fast: 90ms; --motion-activity-duration: 900ms; }\n", encoding="utf-8")
    component.write_text(
        ".fast { transition: opacity 90ms ease, transform 90ms ease; }\n"
        ".standard { transition-duration: 100ms; }\n"
        ".disclosure { transition: color 120ms ease; }\n"
        ".spinner { animation: spin 0.7s linear infinite; }\n"
        ".thinking { animation-duration: 900ms; }\n"
        ".owned { transition: opacity var(--motion-interaction-fast); }\n"
        ".fallback { transition: opacity var(--local-duration, 100ms); }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["00_tokens_base.css", "component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert lint_raw_standard_motion_durations() == [
        "component.css:1: raw standard motion duration 90ms; use var(--motion-interaction-fast)",
        "component.css:2: raw standard motion duration 100ms; use var(--motion-interaction-standard)",
        "component.css:3: raw standard motion duration 120ms; use var(--motion-disclosure)",
        "component.css:4: raw standard motion duration 0.7s; use var(--motion-activity-duration)",
        "component.css:5: raw standard motion duration 900ms; use var(--motion-activity-duration)",
    ]


def test_standard_component_font_size_lint_tree_is_clean():
    assert lint_raw_standard_font_sizes() == []


def test_standard_component_font_size_lint_rejects_fixed_text_but_ignores_fallbacks_and_minimums(monkeypatch, tmp_path):
    tokens = tmp_path / "00_tokens_base.css"
    component = tmp_path / "component.css"
    tokens.write_text(":root { --ui-font-size-2xs: 10px; --ui-font-size-xs: 11px; --ui-font-size-sm: 12px; }\n", encoding="utf-8")
    component.write_text(
        ".ten { font: 700 10px/1 var(--mono-font); }\n"
        ".eleven { font-size: 11px; }\n"
        ".twelve { font: 12px/1.4 sans-serif; }\n"
        ".line-height { font: 700 var(--ui-font-size-3xs)/12px sans-serif; }\n"
        ".fallback { font-size: var(--label-size, 12px); }\n"
        ".minimum { font: 700 max(10px, calc(var(--ui-font-size) - 2px))/1 sans-serif; }\n"
        ".icon { width: 10px; height: 12px; }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["00_tokens_base.css", "component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert lint_raw_standard_font_sizes() == [
        "component.css:1: raw standard component font size 10px; use var(--ui-font-size-2xs)",
        "component.css:2: raw standard component font size 11px; use var(--ui-font-size-xs)",
        "component.css:3: raw standard component font size 12px; use var(--ui-font-size-sm)",
    ]


def test_undefined_css_var_lint_is_clean():
    # every var(--x) in the bundle resolves to a CSS def or a JS setProperty/inline-style.
    assert lint_undefined_css_vars() == []


def test_raw_window_viewport_lint_tree_is_clean():
    assert lint_raw_window_viewport_reads() == []


def test_repeated_raw_component_literal_lint_tree_is_clean():
    assert lint_repeated_raw_component_literals() == []
    assert sb.RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST == {}


def test_opaque_white_has_one_css_paint_owner():
    occurrences = []
    for part in ASSETS["yolomux.css"]:
        text = repo_path(part).read_text(encoding="utf-8")
        occurrences.extend(part for _match in re.finditer(r"(?i)#fff(?:fff)?\b", text))
    assert occurrences == ["static_src/css/yolomux/00_tokens_base.css"]
    assert "--paint-white: #fff;" in repo_path("static_src/css/yolomux/00_tokens_base.css").read_text(encoding="utf-8")


def test_repeated_raw_box_shadow_lint_tree_is_clean():
    assert lint_repeated_raw_box_shadows() == []


def test_z_index_ownership_lint_tree_is_clean():
    assert lint_unowned_z_indexes() == []


def test_tree_row_hover_and_selection_paint_have_one_base_owner():
    tree_css = repo_path("static_src/css/yolomux/50_terminal_file_tree.css").read_text(encoding="utf-8")
    editor_css = repo_path("static_src/css/yolomux/60_editor_file_panels.css").read_text(encoding="utf-8")
    selection_shadow = "box-shadow: inset 4px 0 0 var(--file-selection-border), inset 0 0 0 1px var(--file-selection-outline);"
    hover_shadow = "box-shadow: inset 4px 0 0 var(--file-hover-border), inset 0 0 0 1px var(--file-hover-outline);"
    assert ".file-tree-row.selected,\n.file-tree-row.current-file:not(.selected) {" in tree_css
    assert tree_css.count(selection_shadow) == 1
    assert "body.theme-light .file-tree-row.selected {" not in tree_css
    assert "body.theme-light .file-tree-row.current-file:not(.selected) {" not in tree_css
    assert (tree_css + editor_css).count(hover_shadow) == 1
    assert ".file-tree-row.tabber-row:not(.selected):hover" not in editor_css


def test_repeated_raw_box_shadow_lint_normalizes_whitespace_and_ignores_owned_values(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    component.write_text(
        ".first { box-shadow: 0 0 0 2px var(--ring); }\n"
        ".second { box-shadow:\n  0   0 0 2px   var(--ring); }\n"
        ".none { box-shadow: none; }\n"
        ".important-none { box-shadow: none !important; }\n"
        ".owned { box-shadow: var(--shared-shadow); }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_repeated_raw_box_shadows() == [
        "raw box-shadow '0 0 0 2px var(--ring)' repeats in component.css:1, component.css:2; move it to a CSS token or grouped selector"
    ]


def test_z_index_ownership_lint_checks_css_and_inline_js_styles(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    inline = tmp_path / "inline.js"
    component.write_text(
        ".owned { z-index: var(--z-dialog); }\n"
        ".important { z-index: var(--z-dialog) !important; }\n"
        ".automatic { z-index: auto; }\n"
        ".raw { z-index: 7; }\n"
        ".calculated { z-index: calc(var(--z-dialog) + 1); }\n"
        ".generic { z-index: var(--layer); }\n"
        ".local { --z-local: 9; z-index: var(--z-local); }\n"
        ".local-calculated { --z-local-calculated: calc(var(--z-dialog) + 2); }\n",
        encoding="utf-8",
    )
    inline.write_text("const css = `.inline { z-index: 1000; }`;\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setitem(sb.ASSETS, "yolomux.js", ["inline.js"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert sb.lint_unowned_z_indexes() == [
        "component.css:7: local z-index token --z-local owns '9'; move numeric/arithmetic ownership to 00_tokens_base.css",
        "component.css:8: local z-index token --z-local-calculated owns 'calc(var(--z-dialog) + 2)'; move numeric/arithmetic ownership to 00_tokens_base.css",
        "component.css:4: z-index '7' lacks one named --z-* owner; move literals/arithmetic to 00_tokens_base.css and use var(--z-token)",
        "component.css:5: z-index 'calc(var(--z-dialog) + 1)' lacks one named --z-* owner; move literals/arithmetic to 00_tokens_base.css and use var(--z-token)",
        "component.css:6: z-index 'var(--layer)' lacks one named --z-* owner; move literals/arithmetic to 00_tokens_base.css and use var(--z-token)",
        "inline.js:1: z-index '1000' lacks one named --z-* owner; move literals/arithmetic to 00_tokens_base.css and use var(--z-token)",
    ]


def test_repeated_raw_component_literal_lint_flags_new_repeats(monkeypatch, tmp_path):
    bad = tmp_path / "bad.css"
    bad.write_text(".a { color: #123456; }\n.b { border-color: #123456; }\n.c { color: #abcdef; }\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["bad.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    monkeypatch.setattr(sb, "RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST", {})
    assert sb.lint_repeated_raw_component_literals() == [
        "raw component color #123456 repeats in bad.css:1, bad.css:2; move it to a CSS token or add a reviewed allowlist reason"
    ]


def test_repeated_raw_component_literal_lint_canonicalizes_css_spellings_and_only_masks_var(monkeypatch, tmp_path):
    bad = tmp_path / "bad.css"
    bad.write_text(
        ".alpha-hex { color: #1234; }\n"
        ".alpha-rgb { color: rgba(17 34 51 / 26.6667%); }\n"
        ".mixed { box-shadow: 0 0 #102030; color: var(--fallback, var(--nested, #102030)); }\n"
        ".opaque-rgb { border-color: rgb(16, 32, 48); }\n"
        ".alpha-percent { background: rgb(0 0 0 / 50%); }\n"
        ".alpha-decimal { background: rgba(0, 0, 0, 0.5); }\n"
        ".compound { box-shadow: 0 0 #445566, 0 1px #445566; }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["bad.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    monkeypatch.setattr(sb, "RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST", {})
    assert sb.lint_repeated_raw_component_literals() == [
        "raw component color rgb(0 0 0 / 0.5) repeats in bad.css:5, bad.css:6; move it to a CSS token or add a reviewed allowlist reason",
        "raw component color #102030 repeats in bad.css:3, bad.css:4; move it to a CSS token or add a reviewed allowlist reason",
        "raw component color rgb(17 34 51 / 0.266667) repeats in bad.css:1, bad.css:2; move it to a CSS token or add a reviewed allowlist reason",
        "raw component color #445566 repeats in bad.css:7, bad.css:7; move it to a CSS token or add a reviewed allowlist reason",
    ]


def test_repeated_raw_component_literal_lint_canonicalizes_allowlist_keys(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    component.write_text(".a { color: #ffffff; }\n.b { color: rgb(255 255 255); }\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    monkeypatch.setattr(sb, "RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST", {"#fff": "reviewed white"})
    assert sb.lint_repeated_raw_component_literals() == []


def test_repeated_raw_component_literal_lint_flags_stale_allowlist_entries(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    component.write_text(".only { color: #abcdef; }\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    monkeypatch.setattr(sb, "RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST", {"#abcdef": "reviewed example"})
    assert sb.lint_repeated_raw_component_literals() == [
        "stale raw component color allowlist entry #abcdef: found 1 occurrence(s); remove it"
    ]


def test_raw_token_lint_canonicalizes_opaque_rgb_and_hex(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    component.write_text(".label { color: rgb(17, 24, 39); }\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    monkeypatch.setattr(sb, "_token_opaque_color_values", lambda: {"#111827": ["--lt-text"]})
    assert sb._canonical_opaque_color("#111827") == "#111827"
    assert sb._canonical_opaque_color("rgb(17, 24, 39)") == "#111827"
    assert sb._canonical_opaque_color("rgba(17, 24, 39, 0.5)") is None
    assert lint_raw_literal_equals_token() == [
        "component.css:1: raw color rgb(17, 24, 39) duplicates token value(s) --lt-text; use var(--token)"
    ]


def test_raw_token_lint_masks_only_var_fallback_not_neighboring_literal(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    component.write_text(
        ".label { color: var(--fallback, #111827); border-color: rgb(17 24 39); }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    monkeypatch.setattr(sb, "_token_opaque_color_values", lambda: {"#111827": ["--lt-text"]})
    assert lint_raw_literal_equals_token() == [
        "component.css:1: raw color rgb(17 24 39) duplicates token value(s) --lt-text; use var(--token)"
    ]


def test_raw_token_lint_rejects_opaque_white_property_and_local_token(monkeypatch, tmp_path):
    component = tmp_path / "component.css"
    component.write_text(
        ".surface { background: #fff; }\n"
        ".local { --local-white: #ffffff; color: var(--local-white); }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    monkeypatch.setattr(sb, "_token_opaque_color_values", lambda: {"#ffffff": ["--paint-white"]})
    assert lint_raw_literal_equals_token() == [
        "component.css:1: raw color #fff duplicates token value(s) --paint-white; use var(--token)",
        "component.css:2: raw color #ffffff duplicates token value(s) --paint-white; use var(--token)",
    ]


def test_raw_window_viewport_lint_flags_unowned_reads(monkeypatch, tmp_path):
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
    bad = tmp_path / "bad.css"
    bad.write_text(".a { color: red; } .b {\n", encoding="utf-8")  # truncated rule, missing }
    monkeypatch.setitem(sb.ASSETS, "probe.css", ["__bad__.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: bad if p == "__bad__.css" else sb.REPO_ROOT / p)
    with pytest.raises(BuildError, match="unbalanced CSS braces"):
        sb.check_css_braces()


def test_expected_locale_outputs_raises_on_parity_failure(monkeypatch):
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
