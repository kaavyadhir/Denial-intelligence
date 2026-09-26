"""Settings for the denial intelligence pipeline."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    llm_model: str = "gemini-3.8-flash"
    llm_fallback_models: str = (
        "gemini-3.7-flash,gemini-3.5-flash,gemini-3-flash-preview,gemini-3.5-flash-lite"
    )
    llm_max_attempts: int = 2
    llm_backoff_seconds: float = 1.0

    database_path: str = "data/denials.db"
    raw_data_dir: str = "data/raw"

    # An issuer needs a minimum claim volume before a denial rate means anything.
    # A plan with 40 claims and 20 denials is noise, not a 50% denial rate.
    min_claims_for_analysis: int = 1000
    # Modified z-score threshold. 3.5 is the conventional cut for MAD-based
    # outlier detection; see app/outliers.py for why MAD and not standard z.
    # Statistically extreme on denial rate alone.
    outlier_threshold: float = 3.5
    # Merely elevated. Only reported when a SECOND signal corroborates it -
    # see app/analysis.py. On PY2025 data a 3.5 cut alone surfaced two tiny
    # issuers and discarded every case with appeal evidence behind it.
    watch_threshold: float = 2.0
    # A market needs enough issuers before 'unusual' means anything.
    # MAD over eight values is not evidence.
    min_peers_for_comparison: int = 20

    @property
    def model_chain(self) -> list[str]:
        names = [self.llm_model] + [
            n.strip() for n in self.llm_fallback_models.split(",") if n.strip()
        ]
        seen: set[str] = set()
        return [n for n in names if not (n in seen or seen.add(n))]


settings = Settings()
