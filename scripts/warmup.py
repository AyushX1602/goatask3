"""Pre-recording warmup and readiness check (G6). Run this before hitting
record — it front-loads every one-time cost (ONNX session init, first-call
JIT/graph setup) so the actual take doesn't stall on "loading model..." and
confirms the chain/contract/server preconditions are real, not assumed.

Exit code 0 means every check passed. Non-zero means something needs
fixing before recording, and the specific failing check is printed.

Usage:
    python scripts/warmup.py
    python scripts/warmup.py --skip-chain    # if not anchoring in this take
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import cv2
import numpy as np


def _check(label: str, fn) -> bool:
    start = time.perf_counter()
    try:
        detail = fn()
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  [OK]   {label:<45} {elapsed:6.0f} ms  {detail or ''}")
        return True
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  [FAIL] {label:<45} {elapsed:6.0f} ms  {type(e).__name__}: {e}")
        return False


def check_models_present() -> str:
    from pipeline.config import MODELS_DIR

    required = [
        "face_detection_yunet_2023mar.onnx",
        "w600k_r50.onnx",
    ]
    missing = [m for m in required if not (MODELS_DIR / m).exists()]
    if missing:
        raise FileNotFoundError(
            f"missing {missing} — run `python scripts/fetch_models.py` first"
        )
    return f"{len(required)} models present"


def check_face_detector_loads() -> str:
    from pipeline.face.detect import FaceDetector

    global _detector
    _detector = FaceDetector()
    return "YuNet session initialised"


def check_embedder_loads() -> str:
    from pipeline.face.embed import FaceEmbedder

    global _embedder
    _embedder = FaceEmbedder()
    return "ArcFace w600k_r50 session initialised"


def check_liveness_loads() -> str:
    from pipeline.face.liveness import LivenessChecker

    global _liveness
    _liveness = LivenessChecker()
    return "anti-spoof session initialised"


def check_first_inference_on_a_real_fixture() -> str:
    """Runs one real detect->align->embed pass so the first call's JIT/
    graph-compile cost (measured up to ~800ms on some ONNX Runtime builds)
    happens now, not on the recorded take."""
    from pipeline.face.align import align

    fixture = REPO_ROOT / "tests" / "fixtures" / "obama1.jpg"
    img = cv2.imread(str(fixture))
    if img is None:
        raise FileNotFoundError(f"could not read {fixture}")
    faces = _detector.detect(img)
    if not faces:
        raise RuntimeError("no face detected in the warmup fixture — something is wrong")
    crop = align(img, faces[0].kps5)
    emb = _embedder.embed(crop)
    _liveness.check(img, faces[0])
    assert abs(float(np.linalg.norm(emb.vec)) - 1.0) < 1e-3
    return f"det={faces[0].det_score:.3f}"


def check_gcv_or_serpapi_key_present() -> str:
    from pipeline.config import get_config

    cfg = get_config()
    if cfg.gcv_api_key:
        return "GCV_API_KEY set (primary backend)"
    if cfg.serpapi_key:
        return "SERPAPI_KEY set (secondary backend, needs a public image URL)"
    raise ValueError(
        "no GCV_API_KEY or SERPAPI_KEY set — the recording will silently fall "
        "back to the Bluesky closed-corpus demo, which is NOT the open-web "
        "search the brief asks for. Set at least one before recording."
    )


def check_http_cache_freshness_mode() -> str:
    from pipeline.config import get_config

    cfg = get_config()
    if cfg.http_cache_enabled:
        raise ValueError(
            "HTTP_CACHE=1 — a recorded run would replay from cache, which a "
            "judge can spot in audit.json's cache_hit stats and read as "
            "'canned'. Set HTTP_CACHE=0 for the actual recording take "
            "(docs/rules.md I-10 / R-04)."
        )
    return "HTTP_CACHE=0 — this take will be live, not replayed"


def check_anvil_reachable() -> str:
    from pipeline.chain.evm import EvmClient

    client = EvmClient()
    chain_id = client.w3.eth.chain_id
    if chain_id != 31337:
        raise ValueError(f"connected, but chain_id={chain_id}, expected 31337 (Anvil)")
    return f"chain_id={chain_id}"


def check_contract_deployed() -> str:
    from pipeline.chain.evm import EvmClient

    client = EvmClient()
    contract = client._contract()
    # A cheap read-only call proves the contract is actually deployed at
    # this address on THIS chain, not just that the address string is set.
    exists, _ = contract.functions.verify(b"\x00" * 32).call()
    return f"{contract.address} responds to verify()"


def check_anchored_account_has_funds() -> str:
    from pipeline.chain.evm import EvmClient

    client = EvmClient()
    balance_wei = client.w3.eth.get_balance(client.account.address)
    balance_eth = balance_wei / 1e18
    if balance_wei == 0:
        raise ValueError(f"{client.account.address} has zero balance — anchor() will fail")
    return f"{client.account.address[:10]}... has {balance_eth:.4f} ETH"


def check_sample_runs_committed() -> str:
    from pipeline.config import RUNS_DIR

    runs = sorted(p.name for p in RUNS_DIR.iterdir() if p.is_dir())
    if len(runs) < 2:
        raise ValueError(f"expected >=2 committed sample runs under runs/, found {len(runs)}: {runs}")
    return f"{len(runs)} runs: {', '.join(runs)}"


def check_no_stray_uncommitted_runs() -> str:
    """A common failure mode found repeatedly this session: leftover
    scratch runs from earlier server testing sitting alongside the
    curated sample runs. Not a hard failure — just flagged loudly, since
    it is easy to record a demo against the wrong run_id by mistake."""
    import subprocess

    result = subprocess.run(
        ["git", "status", "--short", "runs/"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    untracked = [line for line in result.stdout.splitlines() if line.startswith("??")]
    if untracked:
        raise ValueError(f"{len(untracked)} untracked run(s) under runs/ — clean these up before recording")
    return "no stray untracked runs"


CHECKS: list[tuple[str, callable]] = [
    ("models present", check_models_present),
    ("face detector loads", check_face_detector_loads),
    ("embedder loads", check_embedder_loads),
    ("liveness model loads", check_liveness_loads),
    ("first real inference (JIT warmup)", check_first_inference_on_a_real_fixture),
    ("search API key present", check_gcv_or_serpapi_key_present),
    ("HTTP_CACHE=0 for this take", check_http_cache_freshness_mode),
    ("Anvil reachable", check_anvil_reachable),
    ("contract deployed", check_contract_deployed),
    ("deployer account funded", check_anchored_account_has_funds),
    ("sample runs committed", check_sample_runs_committed),
    ("no stray uncommitted runs", check_no_stray_uncommitted_runs),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-chain", action="store_true", help="skip Anvil/contract checks")
    args = parser.parse_args()

    print("face-chain-verify — recording warmup & readiness check\n")

    checks = CHECKS
    if args.skip_chain:
        checks = [c for c in checks if "chain" not in c[0] and "contract" not in c[0] and "funded" not in c[0] and "Anvil" not in c[0]]

    results = [_check(label, fn) for label, fn in checks]

    print()
    if all(results):
        print("All checks passed. Ready to record.")
        return 0
    else:
        failed = sum(1 for r in results if not r)
        print(f"{failed} check(s) failed — fix these before recording.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
