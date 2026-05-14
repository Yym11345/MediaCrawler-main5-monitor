from pathlib import Path
import re
from typing import Optional, Union


PathLike = Union[str, Path]


def _safe_platform_name(platform: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(platform).strip().lower()).strip("_")
    if not value:
        raise ValueError("platform cannot be empty")
    return value


def get_browser_profile_dir(
    platform: str,
    root: Optional[PathLike] = None,
    pattern: str = "%s_user_data_dir",
    prefix: str = "",
    base_dir: Optional[PathLike] = None,
) -> Path:
    platform_name = _safe_platform_name(platform)
    folder_name = pattern % platform_name if "%s" in pattern else f"{platform_name}_{pattern}"
    root_path = Path(root or "browser_data").expanduser()

    if not root_path.is_absolute():
        root_path = Path(base_dir or Path.cwd()) / root_path

    return root_path / f"{prefix}{folder_name}"


def get_configured_browser_profile_dir(platform: str, prefix: str = "") -> Path:
    import config

    return get_browser_profile_dir(
        platform=platform,
        root=getattr(config, "BROWSER_PROFILE_ROOT", "browser_data"),
        pattern=getattr(config, "USER_DATA_DIR", "%s_user_data_dir"),
        prefix=prefix,
    )
