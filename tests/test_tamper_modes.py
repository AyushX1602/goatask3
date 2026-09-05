"""Tests for the three tamper modes (T2.2 / D-40):
  1. swap-artifact -> ARTIFACT_MISMATCH
  2. edit-bundle   -> BUNDLE_MODIFIED
  3. forge-bundle  -> NOT_ANCHORED

Asserts all three produce their specific verdicts and the source run directory
remains byte-identical before and after all operations.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from web3 import Web3

from pipeline.chain.evm import EvmClient
from pipeline.chain.tamper import tamper_run
from pipeline.config import RUNS_DIR

ANVIL_RPC = "http://127.0.0.1:8545"


def _anvil_available() -> bool:
    try:
        return Web3(Web3.HTTPProvider(ANVIL_RPC, request_kwargs={"timeout": 2})).is_connected()
    except Exception:
        return False


requires_anvil = pytest.mark.skipif(
    not _anvil_available(), reason="Anvil not running on 127.0.0.1:8545"
)

SAMPLE_RUN_ID = "2026-09-05T18-07-40Z"


def _dir_checksums(d: Path) -> dict[str, str]:
    res = {}
    for f in sorted(d.iterdir()):
        if f.is_file():
            res[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    return res


@requires_anvil
def test_all_three_tamper_modes_and_byte_identity():
    sample_dir = RUNS_DIR / SAMPLE_RUN_ID
    assert sample_dir.exists(), f"sample run {SAMPLE_RUN_ID} must exist"

    before_checksums = _dir_checksums(sample_dir)
    client = EvmClient()

    # 1. swap-artifact
    rep_swap = tamper_run(sample_dir, mode="swap-artifact", client=client)
    assert rep_swap.overall == "ARTIFACT_MISMATCH", rep_swap.detail
    assert rep_swap.mode == "swap-artifact"

    # 2. edit-bundle
    rep_edit = tamper_run(sample_dir, mode="edit-bundle", client=client)
    assert rep_edit.overall == "BUNDLE_MODIFIED", rep_edit.detail
    assert rep_edit.mode == "edit-bundle"

    # 3. forge-bundle
    rep_forge = tamper_run(sample_dir, mode="forge-bundle", client=client)
    assert rep_forge.overall == "NOT_ANCHORED", rep_forge.detail
    assert rep_forge.mode == "forge-bundle"

    # Byte identity invariant: source files MUST NOT have changed
    after_checksums = _dir_checksums(sample_dir)
    assert before_checksums == after_checksums, "source run files were mutated during tamper testing!"
