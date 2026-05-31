from tools.static_build import ASSETS
from tools.static_build import build_asset
from tools.static_build import repo_path


def test_generated_static_assets_are_current():
    for asset in ASSETS:
        assert (repo_path("static") / asset).read_text(encoding="utf-8") == build_asset(asset)
