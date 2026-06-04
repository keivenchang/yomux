import pytest

from tools.static_build import ASSETS
from tools.static_build import BuildError
from tools.static_build import build_asset
from tools.static_build import build_pseudo_catalog
from tools.static_build import check_css_braces
from tools.static_build import locale_key_errors
from tools.static_build import pseudo_value
from tools.static_build import repo_path
from tools.static_build import _color_luminance_alpha
from tools.static_build import _first_color_literal
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


def test_css_braces_are_balanced_in_every_partial():
    # The shipped CSS partials must each be brace-balanced (DOIT.12 B1: a truncated rule once split across
    # two partials and only rebalanced by accident in the bundle). This passes today; it guards regressions.
    check_css_braces()


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


def test_light_mode_lint_tree_is_clean():
    # Durable Fix B (report-only): the shipped CSS has no dark/extreme literal lacking a body.theme-light
    # override, except the vetted LIGHT_LINT_ALLOWLIST. A NEW unpaired dark-box/invisible-text rule fails
    # here — review it, then either fix it (add the light override) or allowlist it with a reason.
    violations = lint_light_mode_pairs()
    assert violations == [], "light-mode pairing regressions:\n  " + "\n  ".join(violations)
