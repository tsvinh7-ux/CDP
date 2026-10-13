import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_sdk_path() -> str:
    return str(Path(__file__).resolve().parents[2] / "sdk" / "web" / "cdp.js")


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "CDP_DATABASE_URL", "postgresql+psycopg://cdp:cdp@localhost:5432/cdp_core"
        )
    )
    admin_token: str = field(default_factory=lambda: os.environ.get("CDP_ADMIN_TOKEN", "change-me"))
    # Maximum clock skew accepted for signed requests, in seconds.
    signature_ttl: int = field(default_factory=lambda: int(os.environ.get("CDP_SIGNATURE_TTL", "300")))
    max_batch_messages: int = 500
    sdk_path: str = field(default_factory=lambda: os.environ.get("CDP_SDK_PATH", _default_sdk_path()))
    # Run the activation/segment jobs inside the API process (handy for a
    # single-node demo; production runs `python -m cdp_core.worker`).
    embedded_worker: bool = field(
        default_factory=lambda: os.environ.get("CDP_EMBEDDED_WORKER", "0") == "1"
    )
    worker_interval: float = field(
        default_factory=lambda: float(os.environ.get("CDP_WORKER_INTERVAL", "10"))
    )


settings = Settings()
