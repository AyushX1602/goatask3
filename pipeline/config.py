"""Single source of truth for configuration.

Loaded from .env with defaults that work with NO .env present — that is what
makes the zero-API-key quickstart possible (prd.md S10, architecture.md 8).

Rules enforced here:
- R-09: MATCH_THRESHOLD / MATCH_MARGIN are read from calibration/threshold.json,
  never from an env var or a literal in pipeline code.
- R-10: no secrets are logged. repr()/str() on Config must never print keys.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
import os

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "models"
CACHE_DIR = REPO_ROOT / ".cache"
RUNS_DIR = REPO_ROOT / "runs"
CALIBRATION_DIR = REPO_ROOT / "calibration"
THRESHOLD_FILE = CALIBRATION_DIR / "threshold.json"

load_dotenv(REPO_ROOT / ".env")


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val if val not in ("", None) else default


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val else default


@dataclass(frozen=True)
class MatchPolicy:
    """Loaded from calibration/threshold.json. Never hand-construct this
    with a literal threshold (R-09) outside of calibrate.py itself."""

    threshold: float
    margin: float
    model: str
    target_fmr: float
    measured_fmr: float | None = None
    measured_tpr: float | None = None
    calibrated_at: str | None = None
    is_placeholder: bool = False


def load_match_policy() -> MatchPolicy:
    """Reads calibration/threshold.json. If it does not exist yet (Phase 2,
    before real calibration in Phase 7), returns a clearly-marked placeholder
    so the pipeline is runnable but the provisional nature is never hidden.
    """
    if THRESHOLD_FILE.exists():
        data = json.loads(THRESHOLD_FILE.read_text(encoding="utf-8"))
        return MatchPolicy(
            threshold=data["threshold"],
            margin=data["margin"],
            model=data["model"],
            target_fmr=data["target_fmr"],
            measured_fmr=data.get("measured_fmr"),
            measured_tpr=data.get("measured_tpr"),
            calibrated_at=data.get("calibrated_at"),
            is_placeholder=False,
        )
    # Provisional only. design.md 3.2 / rules.md R-09 require this be
    # replaced by a derived value before Phase 7 exits.
    return MatchPolicy(
        threshold=0.42,
        margin=0.08,
        model="w600k_r50",
        target_fmr=0.01,
        is_placeholder=True,
    )


def _load_or_create_salt() -> bytes:
    """Face commitment salt (design.md 4.3). Read from env if provided,
    otherwise persisted once under .cache/ so commitments are reproducible
    across runs. Never committed to git (R-01, R-10)."""
    hex_val = _env("FACE_COMMITMENT_SALT_HEX")
    if hex_val:
        return bytes.fromhex(hex_val)

    salt_path = CACHE_DIR / "commitment_salt.bin"
    if salt_path.exists():
        return salt_path.read_bytes()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(32)
    salt_path.write_bytes(salt)
    return salt


@dataclass(frozen=True)
class Config:
    # Search providers — presence of a key is what SearchProvider.available() checks
    serpapi_key: str | None = field(default_factory=lambda: _env("SERPAPI_KEY"))
    azure_vision_key: str | None = field(default_factory=lambda: _env("AZURE_VISION_KEY"))
    azure_vision_endpoint: str | None = field(default_factory=lambda: _env("AZURE_VISION_ENDPOINT"))
    facecheck_key: str | None = field(default_factory=lambda: _env("FACECHECK_KEY"))
    search4faces_key: str | None = field(default_factory=lambda: _env("SEARCH4FACES_KEY"))

    # Bluesky
    bluesky_crawl_limit: int = field(default_factory=lambda: _env_int("BLUESKY_CRAWL_LIMIT", 2000))
    bluesky_seed_handles: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h.strip() for h in (_env("BLUESKY_SEED_HANDLES") or "").split(",") if h.strip()
        )
    )

    # Storage
    pinata_jwt: str | None = field(default_factory=lambda: _env("PINATA_JWT"))

    # Chain (architecture.md 8, R-15: same code path regardless of which chain)
    evm_chain: str = field(default_factory=lambda: _env("EVM_CHAIN", "anvil"))
    evm_rpc_url: str = field(default_factory=lambda: _env("EVM_RPC_URL", "http://127.0.0.1:8545"))
    evm_private_key: str | None = field(default_factory=lambda: _env("EVM_PRIVATE_KEY"))
    evm_contract_address: str | None = field(default_factory=lambda: _env("EVM_CONTRACT_ADDRESS"))

    # Misc
    http_cache_enabled: bool = field(default_factory=lambda: _env("HTTP_CACHE", "1") == "1")

    def __repr__(self) -> str:  # R-10: never print secret values
        def has(v: str | None) -> str:
            return "set" if v else "unset"

        return (
            "Config("
            f"serpapi_key={has(self.serpapi_key)}, "
            f"azure_vision_key={has(self.azure_vision_key)}, "
            f"facecheck_key={has(self.facecheck_key)}, "
            f"search4faces_key={has(self.search4faces_key)}, "
            f"pinata_jwt={has(self.pinata_jwt)}, "
            f"evm_chain={self.evm_chain}, "
            f"evm_private_key={has(self.evm_private_key)}, "
            f"http_cache_enabled={self.http_cache_enabled})"
        )


def get_config() -> Config:
    return Config()


def get_commitment_salt() -> bytes:
    return _load_or_create_salt()


def ensure_dirs() -> None:
    for d in (MODELS_DIR, CACHE_DIR, RUNS_DIR, CALIBRATION_DIR):
        d.mkdir(parents=True, exist_ok=True)
