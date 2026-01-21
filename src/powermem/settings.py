from pathlib import Path
from typing import Optional

from pydantic_settings import SettingsConfigDict


def _get_default_env_file() -> Optional[str]:
    project_root = Path(__file__).resolve().parents[2]
    candidates = (
        Path.cwd() / ".env",
        project_root / ".env",
        project_root / "examples" / "configs" / ".env",
    )
    for path in candidates:
        if path.exists():
            return str(path)
    try:
        from dotenv import find_dotenv

        env_path = find_dotenv(usecwd=True)
        if env_path:
            return env_path
    except Exception:
        pass
    return None


_DEFAULT_ENV_FILE = _get_default_env_file()


def settings_config(
    env_prefix: str = "",
    extra: str = "ignore",
    arbitrary_types_allowed: bool = True,
    env_file: Optional[str] = _DEFAULT_ENV_FILE,
) -> SettingsConfigDict:
    return SettingsConfigDict(
        case_sensitive=False,
        extra=extra,
        env_prefix=env_prefix,
        env_file=env_file,
        env_file_encoding="utf-8",
        arbitrary_types_allowed=arbitrary_types_allowed,
    )
