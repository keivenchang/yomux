import ast
import re
from pathlib import Path

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
from tools.static_build import lint_raw_standard_spacing
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


def test_preformatted_text_wrapping_has_one_shared_selector_owner():
    css = repo_path("static_src/css/yolomux/00_tokens_base.css").read_text(encoding="utf-8")
    selectors = (
        ".tmux-snapshot",
        ".transcript-text",
        ".yoagent-message-body",
        ".conversation-message-body",
        ".yoagent-details-preview",
        ".yoagent-message-details pre",
        ".modal pre",
        ".drop-action-result pre",
        ".file-editor-conflict-compare pre",
    )
    selector_group = ",\n".join(selectors[:-1]) + ",\n" + selectors[-1]
    rule = re.search(rf"{re.escape(selector_group)}\s*\{{([^}}]+)\}}", css)
    assert rule is not None
    assert "white-space: pre-wrap" in rule.group(1)
    assert "overflow-wrap: anywhere" in rule.group(1)
    assert lint_repeated_semantic_declaration_sets() == []


def test_transient_surface_capacities_reuse_the_viewport_clamp():
    css_by_part = {
        part: repo_path(part).read_text(encoding="utf-8")
        for part in ASSETS["yolomux.css"]
    }
    topbar = css_by_part["static_src/css/yolomux/10_topbar_menus.css"]
    popovers = css_by_part["static_src/css/yolomux/20_sessions_popovers.css"]
    file_tree = css_by_part["static_src/css/yolomux/50_terminal_file_tree.css"]
    assert re.search(r"\.topbar-search\s*\{[^}]*flex:\s*1 1 42ch[^}]*max-inline-size:\s*min\(100%, 64ch\)", topbar, re.DOTALL)
    assert re.search(r"\.drag-timing-overlay\s*\{[^}]*max-inline-size:\s*min\(88ch, var\(--popover-max-inline-size\)\)", popovers, re.DOTALL)
    assert re.search(r"\.file-tree-repo-popover\s*\{[^}]*max-inline-size:\s*min\(72ch, var\(--popover-max-inline-size\)\)", file_tree, re.DOTALL)
    assert re.search(r"\.terminal-drop-suggestions\s*\{[^}]*inline-size:\s*min\(64ch, var\(--popover-max-inline-size\)\)[^}]*max-inline-size:\s*var\(--popover-max-inline-size\)", file_tree, re.DOTALL)
    assert "--input-bg is undefined" not in topbar
    for literal in ("max-width: 320px", "max-width: 460px", "max-width: 360px", "max-width: 380px", "min-width: 248px"):
        assert literal not in topbar + popovers + file_tree


def test_dialog_capacities_have_one_content_relative_token_owner():
    tokens = repo_path("static_src/css/yolomux/00_tokens_base.css").read_text(encoding="utf-8")
    topbar = repo_path("static_src/css/yolomux/10_topbar_menus.css").read_text(encoding="utf-8")
    preferences = repo_path("static_src/css/yolomux/30_preferences_changes.css").read_text(encoding="utf-8")
    file_tree = repo_path("static_src/css/yolomux/50_terminal_file_tree.css").read_text(encoding="utf-8")
    panels = repo_path("static_src/css/yolomux/60_editor_file_panels.css").read_text(encoding="utf-8")

    assert "--dialog-compact-inline-size: 80ch" in tokens
    assert "--dialog-standard-inline-size: 112ch" in tokens
    assert "--dialog-wide-inline-size: 144ch" in tokens
    assert topbar.count("width: min(var(--dialog-compact-inline-size), 100%)") == 1
    assert topbar.count("width: min(var(--dialog-wide-inline-size), 100%)") == 1
    assert file_tree.count("width: min(var(--dialog-compact-inline-size), 100%)") == 1
    assert file_tree.count("width: min(var(--dialog-wide-inline-size), 100%)") == 1
    assert "width: min(var(--dialog-standard-inline-size), var(--popover-max-inline-size))" in preferences
    assert preferences.count("width: min(var(--dialog-wide-inline-size), var(--popover-max-inline-size))") == 1
    assert "width: min(var(--dialog-standard-inline-size), 100%)" in panels
    assert "width: min(520px" not in topbar + file_tree
    assert "width: min(760px" not in preferences + panels
    assert "width: min(1180px" not in preferences
    assert "width: min(960px" not in topbar + file_tree
    assert "calc(100% - 28px)" not in topbar


def test_scroll_restoration_browser_checks_wait_for_observable_state():
    source = repo_path("tests/test_browser_layout.py").read_text(encoding="utf-8")

    assert "setTimeout(resolve, 140)" not in source
    assert "setTimeout(resolve, 120)" not in source
    for description in (
        "file editor scroll restoration",
        "Dockview file editor scroll restoration",
        "Preferences scroll restoration",
        "YO!info scroll restoration",
    ):
        assert source.count(f"description: '{description}'") == 1


def test_static_browser_fixture_write_and_navigation_pairs_have_one_owner():
    paths = (
        "tests/test_browser_layout.py",
        "tests/test_browser_editor.py",
        "tests/test_browser_finder.py",
        "tests/test_browser_share.py",
    )

    def is_page_write(statement):
        value = statement.value if isinstance(statement, ast.Expr) else None
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "write_text"
            and isinstance(value.func.value, ast.Name)
            and value.func.value.id == "page"
        )

    def is_page_navigation(statement):
        value = statement.value if isinstance(statement, ast.Expr) else None
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "get"
            and len(value.args) == 1
        ):
            return False
        uri = value.args[0]
        return (
            isinstance(uri, ast.Call)
            and isinstance(uri.func, ast.Attribute)
            and uri.func.attr == "as_uri"
            and isinstance(uri.func.value, ast.Name)
            and uri.func.value.id == "page"
        )

    def statement_bodies(node):
        for _field, value in ast.iter_fields(node):
            if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
                yield value
                for item in value:
                    yield from statement_bodies(item)
            elif isinstance(value, ast.AST):
                yield from statement_bodies(value)

    duplicates = []
    helper_calls_by_path = {}
    for path in paths:
        source = repo_path(path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path)
        helper_calls_by_path[path] = sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "load_static_html_fixture"
            for node in ast.walk(tree)
        )
        for body in statement_bodies(tree):
            for first, second in zip(body, body[1:]):
                if is_page_write(first) and is_page_navigation(second):
                    duplicates.append(f"{path}:{first.lineno}-{second.lineno}")

    assert duplicates == []
    assert all(count > 0 for count in helper_calls_by_path.values()), helper_calls_by_path


def test_browser_fixtures_use_one_read_only_english_catalog_owner():
    helper_path = "tests/browser_helpers/browser_layout.py"
    browser_fixture_paths = (
        "tests/test_browser_editor.py",
        "tests/test_browser_share.py",
        helper_path,
    )
    direct_read = '(REPO_ROOT / "static" / "locales" / "en.json").read_text'
    sources = {path: repo_path(path).read_text(encoding="utf-8") for path in browser_fixture_paths}

    assert sources[helper_path].count(direct_read) == 1
    assert "MappingProxyType(catalog)" in sources[helper_path]
    assert "def app_english_strings() -> Mapping[str, str]:" in sources[helper_path]
    for path in browser_fixture_paths[:-1]:
        assert direct_read not in sources[path], path
        assert "app_english_strings()" in sources[path], path


def test_event_rows_use_one_container_responsive_layout_owner():
    css = repo_path("static_src/css/yolomux/50_terminal_file_tree.css").read_text(encoding="utf-8")

    assert re.search(r"\.event-list\s*\{[^}]*container:\s*event-list / inline-size", css, re.DOTALL)
    assert re.search(r"\.event-item\s*\{[^}]*grid-template-columns:\s*minmax\(18ch, max-content\) minmax\(12ch, 20ch\) minmax\(0, 1fr\)", css, re.DOTALL)
    assert re.search(r"@container event-list \(max-width: 72ch\)\s*\{[\s\S]*?\.event-message\s*\{[^}]*grid-column:\s*1 / -1", css)
    assert "grid-template-columns: 132px 118px minmax(0, 1fr)" not in css


def test_browser_fixture_wait_loops_have_one_injected_owner():
    browser_fixture_paths = (
        "tests/test_browser_share.py",
        "tests/test_browser_layout.py",
        "tests/test_browser_editor.py",
        "tests/test_browser_dockview.py",
    )
    fixture_sources = {
        path: repo_path(path).read_text(encoding="utf-8")
        for path in browser_fixture_paths
    }
    helper = repo_path("tests/browser_helpers/browser_layout.py").read_text(encoding="utf-8")

    assert "BROWSER_WAIT_HELPER_SOURCE" in helper
    assert "Page.addScriptToEvaluateOnNewDocument" in helper
    assert "Timed out after ${timeoutMs}ms waiting for ${description}" in helper
    assert sum(source.count("const waitFor = window.__yolomuxTestWaitFor;") for source in fixture_sources.values()) == 40
    for path, source in fixture_sources.items():
        assert "const waitFor = async" not in source, path
        assert "const waitFor = predicate" not in source, path
        assert "await delay(320)" not in source, path
    assert "file-editor-preview-zoom-measuring'); i += 1" not in fixture_sources["tests/test_browser_editor.py"]
    assert "description: 'split Mermaid zoom measurement'" in fixture_sources["tests/test_browser_editor.py"]
    assert "description: 'bright Mermaid zoom measurement'" in fixture_sources["tests/test_browser_editor.py"]

    layout_tree = ast.parse(fixture_sources["tests/test_browser_layout.py"], filename="tests/test_browser_layout.py")
    rename_test = next(
        ast.get_source_segment(fixture_sources["tests/test_browser_layout.py"], node) or ""
        for node in ast.walk(layout_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "test_rename_marks_index_building_and_refresh_done_requeries_open_search"
    )
    assert "setTimeout(resolve, 0)" not in rename_test
    assert "description: 'renamed root index building'" in rename_test


def test_yostats_history_browser_tests_use_shared_observable_waits():
    path = repo_path("tests/test_browser_layout.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "test_debug_graph_wider_range_fetches_and_paints_older_history_after_inflight_poll",
        "test_debug_graph_history_error_retains_chart_and_retry_clears_overlay",
        "test_debug_graph_chrome_refocus_fetches_missed_history_and_redraws_immediately",
    }
    function_sources = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    }

    assert set(function_sources) == names
    for name, function_source in function_sources.items():
        assert "window.__yolomuxTestWaitFor" in function_source, name
        assert "const deadline = performance.now()" not in function_source, name
        assert "setTimeout(resolve, 650)" not in function_source, name
    assert function_sources["test_debug_graph_wider_range_fetches_and_paints_older_history_after_inflight_poll"].count(
        'assert metrics["historyResolution"] == 1'
    ) == 1


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
    assert ":where(body.theme-light) .changes-refresh," not in preferences_css
    assert not re.search(r"body\.theme-light \.preferences-setting-control input\[type=\"number\"\],[\s\S]*?body\.theme-light \.diff-ref-suggestion-popover\s*\{[^}]*color:", preferences_css)
    assert "--20-sessions-popovers-ci-indicator-bg-5: var(--paint-white);" in tokens_css
    assert ".ci-indicator:not(.branch-indicator):not(.pr-number-chip)" in popovers_css
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


def test_audited_css_families_have_one_grouped_owner():
    layout_css = repo_path("static_src/css/yolomux/40_layout_panes_tabs.css").read_text(encoding="utf-8")
    preferences_css = repo_path("static_src/css/yolomux/30_preferences_changes.css").read_text(encoding="utf-8")
    file_tree_css = repo_path("static_src/css/yolomux/50_terminal_file_tree.css").read_text(encoding="utf-8")
    panels_css = repo_path("static_src/css/yolomux/60_editor_file_panels.css").read_text(encoding="utf-8")

    assert ".preferences-search,\n.search-history-input {" in preferences_css
    assert ".changes-summary-totals,\n.changes-repo-totals {" in preferences_css
    assert ".keyboard-shortcut-row,\n.keyboard-legend-row {" in preferences_css
    assert ".info-tree-search-control input:focus,\n.preferences-search:focus,\n.search-history-input:focus {" in file_tree_css
    assert ".conversation-send,\n.yoagent-chat-stop {" in file_tree_css
    assert ".file-editor-preview-pane,\n.file-editor-preview-pane-panel {" in panels_css
    assert ".file-explorer-title,\n.file-explorer-panel-title,\n.file-editor-title {" in panels_css
    assert re.search(r"\.pane-drag-image-frame,\s*\.preferences-panel,\s*\.js-debug-panel,\s*\.command-palette-dialog,\s*\.panel,\s*\.transcript,\s*\.summary\s*\{", preferences_css)
    assert re.search(r"\.pane-tab-close::before,\s*\.pane-tab-close::after,\s*\.panel-detail-close::before,\s*\.panel-detail-close::after,[\s\S]*?\.file-editor-panel-close::after\s*\{", preferences_css)
    assert re.search(r"\.file-editor-codemirror \.cm-content ::selection,\s*\.file-editor-codemirror-panel \.cm-content ::selection\s*\{", panels_css)
    assert ".cm-content ::-moz-selection" not in panels_css
    assert not re.search(r"\.panel\s*\{[^}]*grid-template-rows:", layout_css)
    assert not re.search(r"\.panel\.file-editor-panel\s*\{[^}]*grid-template-rows:", panels_css)


def test_code_syntax_color_ownership_tree_is_clean():
    assert lint_code_syntax_color_ownership() == []


def test_runtime_buttons_have_one_shared_construction_owner():
    assert sb.lint_direct_button_construction() == []


def test_shared_ui_ownership_map_and_agent_reuse_protocol_remain_routable():
    development = repo_path("docs/DEVELOPMENT.md").read_text(encoding="utf-8")
    agents = repo_path("AGENTS.md").read_text(encoding="utf-8")
    readme = repo_path("README.md").read_text(encoding="utf-8")
    assert "## Shared UI Ownership Map" in development
    for symbol in (
        "TAB_TYPES",
        "bindPanelShell()",
        "bindActionDispatcher()",
        "sessionAgentWindowStatusSummary()",
        "createSharedTreeInteractionController()",
        "createHoverPopover()",
        "notificationEventDefinitions",
        "conversationMessageShellHtml()",
        "jsDebugGraphChartGroups",
        "dragPayload()",
        "YoagentController",
        "### Central State Containers",
    ):
        assert symbol in development
    assert "Before an implementation edit: search for existing behavior owners" in agents
    assert "Existing parent checked: <symbol>" in development
    assert "Shared UI Ownership Map" not in readme


def test_panel_frame_builder_owns_the_shared_panel_chrome():
    owner = repo_path("static_src/js/yolomux/78_panel_shell.js").read_text(encoding="utf-8")
    assert "function panelFrameHtml(" in owner
    assert "panel-toast-stack" in owner
    assert "pane-tabs\" role=\"tablist\"" in owner
    for path in (
        "static_src/js/yolomux/78_panel_shell.js",
        "static_src/js/yolomux/80_info_panel.js",
        "static_src/js/yolomux/82_chat_panel.js",
        "static_src/js/yolomux/82_preferences_panel.js",
        "static_src/js/yolomux/83_debug_panel.js",
        "static_src/js/yolomux/90_changes_editor.js",
        "static_src/js/yolomux/99_terminal_boot.js",
    ):
        source = repo_path(path).read_text(encoding="utf-8")
        if path != "static_src/js/yolomux/78_panel_shell.js":
            assert "panelFrameHtml({" in source
            assert '<div class="panel-head' not in source


def test_direct_button_construction_lint_rejects_parallel_builders(monkeypatch, tmp_path):
    owner = tmp_path / "10_core_utils.js"
    parallel = tmp_path / "20_parallel.js"
    owner.write_text("function makeButton() {\n  return document.createElement('button');\n}\n", encoding="utf-8")
    parallel.write_text("function localButton() {\n  return document.createElement(\"button\");\n}\n", encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.js", ["10_core_utils.js", "20_parallel.js"])
    monkeypatch.setattr(sb, "repo_path", lambda path: tmp_path / path)

    assert sb.lint_direct_button_construction() == [
        "20_parallel.js:2: direct button construction bypasses makeButton()"
    ]


def test_shared_ui_ownership_lint_rejects_a_raw_pane_control_family(monkeypatch, tmp_path):
    terminal = tmp_path / "99_terminal_boot.js"
    terminal.write_text(
        "function paneFrameControlsHtml() {\n"
        "  return '<button type=\"button\">x</button>';\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sb, "SHARED_UI_OWNERSHIP_REQUIREMENTS", {
        "99_terminal_boot.js": (("pane-frame controls", "function paneFrameControlsHtml", "toolbarButtonHtml("),),
    })
    monkeypatch.setattr(sb, "repo_path", lambda path: tmp_path / Path(path).name)

    errors = sb.lint_shared_ui_ownership()

    assert "99_terminal_boot.js: shared pane-frame controls owner is missing 'toolbarButtonHtml('" in errors
    assert "static_src/js/yolomux/99_terminal_boot.js: paneFrameControlsHtml() must use toolbarButtonHtml(), not raw button templates" in errors


def test_node_shard_launcher_has_unique_behavior_owners_and_a_terminal_summary():
    launcher = repo_path("tests/layout_url.test.js").read_text(encoding="utf-8")
    helper = repo_path("tests/layout_test_helper.js").read_text(encoding="utf-8")
    suite_files = re.findall(r"'tests/[^']+\.test\.js'", launcher)

    assert len(suite_files) == len(set(suite_files)) == 9
    assert "layout suite shards:" in launcher
    assert "if (failed) process.exitCode = 1" in launcher
    assert "DID NOT SETTLE" in helper
    assert "layout suite: ${__testPass} passed, ${__testFail} failed" in helper


def test_normalized_production_clone_lint_rejects_new_unreviewed_behavior_copy(monkeypatch, tmp_path):
    body = "\n".join((
        "function copiedBehavior() {",
        "  const state = readState();",
        "  if (!state) return null;",
        "  const next = normalize(state);",
        "  saveState(next);",
        "  emitChange(next);",
        "  return next;",
        "}",
        "",
    ))
    (tmp_path / "a.js").write_text(body, encoding="utf-8")
    (tmp_path / "b.js").write_text(body.replace("copiedBehavior", "anotherBehavior"), encoding="utf-8")
    monkeypatch.setitem(sb.ASSETS, "yolomux.js", ["a.js", "b.js"])
    monkeypatch.setattr(sb, "repo_path", lambda path: tmp_path / path)
    monkeypatch.setattr(sb, "NORMALIZED_PRODUCTION_CLONE_ALLOWLIST", {})

    errors = sb.lint_normalized_production_clones(window=6)

    assert len(errors) == 1
    assert errors[0].startswith("normalized production clone ")
    assert "across a.js, b.js" in errors[0]
    key = errors[0].removeprefix("normalized production clone ").split(";", 1)[0]
    digest = key.split(" across ", 1)[0]
    monkeypatch.setattr(sb, "NORMALIZED_PRODUCTION_CLONE_ALLOWLIST", {f"a.js, b.js:{digest}": "fixture proves reviewed baseline suppression"})
    assert sb.lint_normalized_production_clones(window=6) == []


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
        (name, tuple(f"{property_name}: {value}" for property_name, value in sorted(declarations)))
        for name, declarations in sorted(sb.SHARED_CSS_DECLARATION_SETS.items())
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


def test_standard_spacing_lint_tree_is_clean():
    assert lint_raw_standard_spacing() == []


def test_standard_spacing_lint_rejects_physical_logical_compound_and_fallback_copies(monkeypatch, tmp_path):
    tokens = tmp_path / "00_tokens_base.css"
    component = tmp_path / "component.css"
    tokens.write_text(":root { --space-4: 4px; --space-8: 8px; --space-12: 12px; }\n", encoding="utf-8")
    component.write_text(
        ".scalar { gap: 8px; }\n"
        ".compound { gap: 4px 8px; }\n"
        ".row { row-gap: 12px; }\n"
        ".column { column-gap: var(--local-gap, 4px); }\n"
        ".padding { padding: 2px 4px 16px; }\n"
        ".logical { padding-inline-start: 8px; margin-block: var(--local-margin, 6px) auto; }\n"
        ".margin { margin: 0 5px; }\n"
        ".invalid-negative { margin-inline-start: -var(--space-4); }\n"
        ".owned { gap: var(--space-8); }\n"
        ".owned-box { padding: var(--space-2) var(--space-4); margin: 0; }\n"
        ".custom { gap: 16px; }\n"
        ".custom-box { padding: 14px; margin: 18px; }\n"
        ".relative { gap: 0.45em; }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["00_tokens_base.css", "component.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    assert lint_raw_standard_spacing() == [
        "component.css:1: raw standard spacing 8px; use var(--space-8)",
        "component.css:2: raw standard spacing 4px; use var(--space-4)",
        "component.css:2: raw standard spacing 8px; use var(--space-8)",
        "component.css:3: raw standard spacing 12px; use var(--space-12)",
        "component.css:4: raw standard spacing 4px; use var(--space-4)",
        "component.css:5: raw standard spacing 2px; use var(--space-2)",
        "component.css:5: raw standard spacing 4px; use var(--space-4)",
        "component.css:6: raw standard spacing 8px; use var(--space-8)",
        "component.css:6: raw standard spacing 6px; use var(--space-6)",
        "component.css:7: raw standard spacing 5px; use var(--space-5)",
        "component.css:8: invalid negated spacing token -var(--space-4); use calc(-1 * var(--space-4))",
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


def test_linked_semantic_colors_alias_one_palette_owner():
    css = repo_path("static_src/css/yolomux/00_tokens_base.css").read_text(encoding="utf-8")

    for declaration in (
        "--editor-scheme-panel: var(--panel);",
        "--editor-scheme-gutter-bg: var(--panel);",
        "--pane-tab-panel-head-bg: var(--brand-green-dark);",
        "--pane-meta-path: var(--pane-tab-text);",
        "--pc-control-fg: var(--pane-tab-text);",
        "--pc-control-border: var(--pane-tab-control-border);",
        "--git-deleted-badge: var(--danger-strong);",
        "--bad: var(--danger-strong-hover);",
        "--code-function: var(--link-soft);",
        "--pr-status-passing: var(--good);",
        "--code-inline: var(--code-inline-dark-text);",
        "--code-inline-bg: var(--code-inline-dark-bg);",
        "--code-inline-border: var(--code-inline-dark-border);",
        "--markdown-html-dark-code: var(--code-inline-dark-text);",
        "--markdown-html-dark-code-bg: var(--code-inline-dark-bg);",
        "--markdown-html-dark-code-border: var(--code-inline-dark-border);",
        "--lt-markdown-link: var(--light-link-text);",
        "--markdown-html-light-link: var(--light-link-text);",
        "--lt-code-inline: var(--code-inline-light-text);",
        "--lt-code-inline-bg: var(--code-inline-light-bg);",
        "--lt-code-inline-border: var(--code-inline-light-border);",
        "--markdown-html-light-code: var(--code-inline-light-text);",
        "--markdown-html-light-code-bg: var(--code-inline-light-bg);",
        "--markdown-html-light-code-border: var(--code-inline-light-border);",
        "--lt-code-number: var(--lt-code-atom);",
        "--lt-code-tag: var(--lt-code-keyword);",
        "--lt-code-comment: var(--lt-muted);",
        "--editor-preview-bg: var(--editor-scheme-preview-bg);",
        "--markdown-html-dark-border: var(--markdown-html-dark-bg);",
        "--share-stage-bg: var(--paint-black);",
        "--pane-resizer-shadow: var(--pane-resizer-bg);",
    ):
        assert declaration in css
    assert css.count("#151922") == 1
    assert css.count("#dfe6ef") == 2  # one owner plus the explanatory light-theme comment
    assert css.count("#3f6f00") == 2  # dark owner plus the intentionally distinct light git-staged value
    assert css.count("#075985") == 1
    assert css.count("#a40e26") == 1
    assert css.count("#fff1d6") == 1
    assert css.count("#d8a657") == 1
    assert css.count("#000") == 1
    for inherited in ("--auto-text", "--danger-text", "--danger-bg", "--danger-border", "--danger-muted-bg", "--danger-strong", "--danger-strong-hover", "--danger-strong-border", "--danger-light-text"):
        assert css.count(f"{inherited}:") == 1


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


def test_repeated_raw_component_literal_lint_owns_variable_channel_alpha(monkeypatch, tmp_path):
    bad = tmp_path / "bad.css"
    bad.write_text(
        ".a { color: rgb(var(--theme-rgb) / 0.2); }\n"
        ".b { border-color: rgba(var(--theme-rgb) / 20%); }\n"
        ".c { color: rgb(var(--other-rgb) / 0.2); }\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sb.ASSETS, "yolomux.css", ["bad.css"])
    monkeypatch.setattr(sb, "repo_path", lambda p: tmp_path / p)
    monkeypatch.setattr(sb, "RAW_COMPONENT_LITERAL_REPEAT_ALLOWLIST", {})

    assert sb.lint_repeated_raw_component_literals() == [
        "raw component color rgb(var(--theme-rgb) / 0.2) repeats in bad.css:1, bad.css:2; move it to a CSS token"
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
