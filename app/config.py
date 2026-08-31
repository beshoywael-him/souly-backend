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

    # --- Image generation ---------------------------------------------------
    # The pictures above each lesson. Separate settings from the tutor's
    # because they are separate products with separate billing: the text model
    # runs on a free tier quite happily, image generation does not, and the
    # two are worth being able to point at different keys.
    #
    #   IMAGE_GENERATOR_PROVIDER   'google', or 'none' to switch pictures off
    #   IMAGE_GENERATOR_API_KEY    defaults to GEMINI_API_KEY when blank
    #   IMAGE_GENERATOR_MODEL      imagen-3.0-generate-002, or a gemini *-image
    #   IMAGE_ASPECT_RATIO         1:1, 16:9, 9:16, 4:3, 3:4
    #
    # 16:9 by default because the picture sits as a band above the lesson
    # text, and a square one pushes the words off a tablet screen.
    image_generator_provider: str = "google"
    image_generator_api_key: str = ""
    image_generator_model: str = "imagen-3.0-generate-002"
    image_aspect_ratio: str = "16:9"

    # --- The picture that moves ---------------------------------------------
    # Two generated frames cross-faded into a loop, so the child watches the
    # seed become a sprout instead of reading that it did. Costs a second
    # image call per scene; falls back to the still if the second frame does
    # not come back.
    #
    #   ILLUSTRATION_MOTION         true / false
    #   ILLUSTRATION_MOTION_FORMAT  webp or gif
    #
    # WHY WEBP AND NOT GIF BY DEFAULT
    # -------------------------------
    # They are the same thing to a child — a picture that moves — and every
    # browser this app runs on plays both. They are not the same thing to the
    # router: measured on a shaded illustration, the same loop is 386 KB as a
    # GIF and 68 KB as WebP, because GIF has a 256-colour palette and a soft
    # dissolve is the worst case for it. The lesson text is held back until
    # this file arrives, so its size is time a child spends waiting. Set
    # ILLUSTRATION_MOTION_FORMAT=gif if a real .gif is wanted anyway.
    illustration_motion: bool = True
    illustration_motion_format: str = "webp"

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

    @property
    def image_key(self) -> str:
        """The image key, falling back to the tutor's when one key does both."""
        return self.image_generator_api_key or self.gemini_api_key

    @property
    def image_configured(self) -> bool:
        return bool(self.image_key
                    and self.image_generator_provider.lower() == "google")

    @property
    def motion_format(self) -> str:
        fmt = self.illustration_motion_format.strip().lower()
        return fmt if fmt in ("webp", "gif") else "webp"

    @property
    def motion_configured(self) -> bool:
        return bool(self.illustration_motion and self.image_configured)


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once per process."""
    return Settings()


settings = get_settings()
