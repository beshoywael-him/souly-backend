"""
Souly configuration.

Every tunable value and every secret is read here, from the environment or
from `.env`. Nothing else in the codebase calls os.getenv() directly — so when
the TTS vendor is finally chosen, exactly one file changes.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---------------------------------------------------------------
    souly_env: str = "development"
    souly_db_path: str = "./data/souly.db"
    souly_host: str = "0.0.0.0"
    souly_port: int = 8000
    souly_cors_origins: str = "*"

    # --- LLM (Phase 2) ------------------------------------------------------
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-pro"

    # --- STT: ElevenLabs (Phase 2) ------------------------------------------
    elevenlabs_api_key: str = ""
    elevenlabs_stt_model: str = "scribe_v1"

    # --- TTS: vendor undecided (Phase 2) ------------------------------------
    # Intentionally blank. Filled in when the team picks a provider.
    tts_provider: str = ""
    tts_api_key: str = ""
    tts_voice_id: str = ""
    tts_model: str = ""

    # --- RAG (Phase 2) ------------------------------------------------------
    chroma_persist_dir: str = "./data/chroma"
    embedding_model: str = "all-MiniLM-L6-v2"

    # --- Curriculum ---------------------------------------------------------
    # Where the Ministry PDFs live. They are the canon and they stay on disk:
    # curriculum_pages maps lesson -> page and holds no text at all, so this
    # directory is the only copy of the actual content.
    souly_curriculum_dir: str = "./data/curriculum"

    # --- Flag pipeline ------------------------------------------------------
    flag_min_confidence: float = 0.5
    flag_auto_approve: bool = False

    # --- Derived ------------------------------------------------------------
    @property
    def db_file(self) -> Path:
        """Absolute path to the SQLite file, with its parent directory ensured."""
        p = Path(self.souly_db_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def schema_file(self) -> Path:
        return PROJECT_ROOT / "schema.sql"

    @property
    def schema_v2_file(self) -> Path:
        return PROJECT_ROOT / "schema_v2.sql"

    @property
    def static_dir(self) -> Path:
        return PROJECT_ROOT / "static"

    @property
    def curriculum_dir(self) -> Path:
        """Absolute path to the directory holding the Ministry PDFs."""
        p = Path(self.souly_curriculum_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def curriculum_cache_dir(self) -> Path:
        """
        Derived artefacts: one text file and one PNG per ingested page.

        Everything in here is regenerable from the PDFs by re-running
        scripts/ingest_curriculum.py, so it is safe to delete and it is not
        the source of truth for anything.
        """
        return self.curriculum_dir / ".cache"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.souly_cors_origins.split(",") if o.strip()]

    # --- Readiness checks used by /health -----------------------------------
    @property
    def stt_configured(self) -> bool:
        return bool(self.elevenlabs_api_key)

    @property
    def tts_configured(self) -> bool:
        return bool(self.tts_provider and self.tts_api_key)

    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key)


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once per process."""
    return Settings()


settings = get_settings()
