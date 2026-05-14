from pathlib import Path

from tools.browser_profile import get_browser_profile_dir


def test_browser_profile_uses_fixed_platform_folder(tmp_path):
    profile_dir = get_browser_profile_dir(
        "dy",
        root="browser_data",
        base_dir=tmp_path,
    )

    assert profile_dir == tmp_path / "browser_data" / "dy_user_data_dir"


def test_browser_profile_separates_supported_platforms(tmp_path):
    profile_dirs = {
        platform: get_browser_profile_dir(
            platform,
            root="browser_data",
            base_dir=tmp_path,
        )
        for platform in ("dy", "xhs", "bili")
    }

    assert profile_dirs["dy"].name == "dy_user_data_dir"
    assert profile_dirs["xhs"].name == "xhs_user_data_dir"
    assert profile_dirs["bili"].name == "bili_user_data_dir"
    assert len(set(profile_dirs.values())) == 3


def test_browser_profile_sanitizes_platform_name(tmp_path):
    profile_dir = get_browser_profile_dir(
        "DY../XHS",
        root=Path("browser_data"),
        base_dir=tmp_path,
    )

    assert profile_dir == tmp_path / "browser_data" / "dy_xhs_user_data_dir"


def test_browser_profile_supports_cdp_prefix(tmp_path):
    profile_dir = get_browser_profile_dir(
        "bili",
        root=tmp_path,
        prefix="cdp_",
    )

    assert profile_dir == tmp_path / "cdp_bili_user_data_dir"
