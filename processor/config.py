from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_dirs(value: str | None) -> tuple[str, ...]:
    if not value:
        return ("/Pictures/Camera Roll",)
    dirs = [part.strip() for part in value.split(",") if part.strip()]
    return tuple(dirs) if dirs else ("/Pictures/Camera Roll",)


def load_dotenv_file(env_path: Path) -> None:
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_val = stripped.split("=", 1)
        key = key.strip()
        val = raw_val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass(frozen=True)
class Config:
    ms_client_id: str
    ms_client_secret: str
    ms_tenant: str
    ms_refresh_token: str
    onedrive_source_dirs: tuple[str, ...]
    onedrive_trash_mode: str
    onedrive_staging_dir: str

    gphotos_enabled: bool
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str

    ai_provider: str
    ai_api_key: str
    ai_model: str
    ai_base_url: str

    worker_base_url: str
    processor_token: str

    burst_window_seconds: int
    dhash_max_distance: int
    include_singletons: bool
    local_state_db: Path

    @classmethod
    def from_env(cls, dotenv_path: str | Path | None = None) -> "Config":
        if dotenv_path is not None:
            load_dotenv_file(Path(dotenv_path))
        elif Path(".env").is_file():
            load_dotenv_file(Path(".env"))

        trash_mode = (os.getenv("ONEDRIVE_TRASH_MODE") or "recycle_bin").strip().lower()
        if trash_mode not in {"recycle_bin", "staging_folder"}:
            raise ValueError(f"Unsupported ONEDRIVE_TRASH_MODE: {trash_mode}")

        ai_provider = (os.getenv("AI_PROVIDER") or "gemini").strip().lower()
        default_model = "gemini-2.5-flash" if ai_provider == "gemini" else "gpt-4o-mini"

        return cls(
            ms_client_id=os.getenv("MS_CLIENT_ID", "").strip(),
            ms_client_secret=os.getenv("MS_CLIENT_SECRET", "").strip(),
            ms_tenant=(os.getenv("MS_TENANT") or "consumers").strip(),
            ms_refresh_token=os.getenv("MS_REFRESH_TOKEN", "").strip(),
            onedrive_source_dirs=_parse_dirs(os.getenv("ONEDRIVE_SOURCE_DIRS")),
            onedrive_trash_mode=trash_mode,
            onedrive_staging_dir=(os.getenv("ONEDRIVE_STAGING_DIR") or "/_PhotoCull_Trash").strip(),
            gphotos_enabled=_parse_bool(os.getenv("GPHOTOS_ENABLED"), True),
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
            google_refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN", "").strip(),
            ai_provider=ai_provider,
            ai_api_key=os.getenv("AI_API_KEY", "").strip(),
            ai_model=(os.getenv("AI_MODEL") or default_model).strip(),
            ai_base_url=os.getenv("AI_BASE_URL", "").strip().rstrip("/"),
            worker_base_url=os.getenv("WORKER_BASE_URL", "").strip().rstrip("/"),
            processor_token=os.getenv("PROCESSOR_TOKEN", "").strip(),
            burst_window_seconds=int(os.getenv("BURST_WINDOW_SECONDS") or "90"),
            dhash_max_distance=int(os.getenv("DHASH_MAX_DISTANCE") or "12"),
            include_singletons=_parse_bool(os.getenv("INCLUDE_SINGLETONS"), True),
            local_state_db=Path(os.getenv("LOCAL_STATE_DB") or "./data/state.sqlite3"),
        )
