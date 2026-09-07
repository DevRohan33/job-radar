"""Configuration: env secrets plus the three files under profile/."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = ROOT / "profile"
STATE_DIR = ROOT / "state"
JD_CACHE_DIR = STATE_DIR / "jd_cache"


def _load_dotenv(path: Path | None = None) -> None:
    """Read .env for local runs. Real environment variables always win, so this
    is a no-op in GitHub Actions where secrets are already exported."""
    env_file = path or ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass
class Settings:
    # Stage 1 - Gmail IMAP
    gmail_user: str = field(default_factory=lambda: _env("GMAIL_USER"))
    gmail_app_password: str = field(
        default_factory=lambda: _env("GMAIL_APP_PASSWORD").replace(" ", "")
    )
    imap_host: str = field(default_factory=lambda: _env("IMAP_HOST", "imap.gmail.com"))
    imap_folder: str = field(default_factory=lambda: _env("IMAP_FOLDER", "INBOX"))
    lookback_days: int = field(default_factory=lambda: _env_int("LOOKBACK_DAYS", 2))

    # Stage 3 - enrichment
    enrich_enabled: bool = field(default_factory=lambda: _env_bool("ENRICH_ENABLED", True))
    enrich_top_n: int = field(default_factory=lambda: _env_int("ENRICH_TOP_N", 12))
    enrich_min_delay: float = field(
        default_factory=lambda: float(_env("ENRICH_MIN_DELAY", "2.5"))
    )
    enrich_max_delay: float = field(
        default_factory=lambda: float(_env("ENRICH_MAX_DELAY", "6.0"))
    )

    # Stage 4 - scoring
    llm_enabled: bool = field(default_factory=lambda: _env_bool("LLM_ENABLED", False))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o-mini"))
    # Any OpenAI-compatible endpoint (Groq, Together, a local server). Blank = OpenAI.
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_top_n: int = field(default_factory=lambda: _env_int("LLM_TOP_N", 15))

    # Stage 5 - sheets
    sheet_id: str = field(default_factory=lambda: _env("SHEET_ID"))
    sheet_tab: str = field(default_factory=lambda: _env("SHEET_TAB", "Jobs"))
    google_service_account_json: str = field(
        default_factory=lambda: _env("GOOGLE_SERVICE_ACCOUNT_JSON")
    )
    # Local convenience: point at the downloaded key file instead of pasting JSON.
    google_service_account_file: str = field(
        default_factory=lambda: _env("GOOGLE_SERVICE_ACCOUNT_FILE")
    )

    # Phase 4 - digest
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    digest_min_score: int = field(default_factory=lambda: _env_int("DIGEST_MIN_SCORE", 70))

    def require_gmail(self) -> None:
        missing = [
            n
            for n, v in (("GMAIL_USER", self.gmail_user), ("GMAIL_APP_PASSWORD", self.gmail_app_password))
            if not v
        ]
        if missing:
            raise RuntimeError(
                f"Missing env var(s): {', '.join(missing)}. "
                "See SETUP.md - Gmail needs an App Password, not your login password."
            )

    def require_sheets(self) -> None:
        missing = []
        if not self.sheet_id:
            missing.append("SHEET_ID")
        if not (self.google_service_account_json or self.google_service_account_file):
            missing.append("GOOGLE_SERVICE_ACCOUNT_JSON (or GOOGLE_SERVICE_ACCOUNT_FILE)")
        if missing:
            raise RuntimeError(f"Missing env var(s): {', '.join(missing)}. See SETUP.md.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
