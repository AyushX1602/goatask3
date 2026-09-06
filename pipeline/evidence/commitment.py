"""Salted face commitment. See docs/design.md 4.3, R-01.

R-01 is the most important privacy rule in this project: no face embedding,
and no reversible derivative of one, may ever be written to a blockchain,
uploaded, or committed to git. An immutable public ledger plus a
recoverable biometric template is a permanent, un-deletable biometric
record — a direct collision with GDPR Art. 9 and India's DPDP Act.

The commitment below still proves "this same face produced this record"
without ever publishing anything biometric: keccak256(salt || quantized
embedding). Reproducing it requires the salt, which lives only in
.cache/commitment_salt.bin or FACE_COMMITMENT_SALT_HEX (config.py,
never committed).

Quantisation matters for a reason beyond "int is smaller than float": the
same face re-embedded twice will not produce bit-identical float32 values
(different JPEG re-encode, different alignment rounding, etc.), so hashing
the raw vector would make the SAME face produce a DIFFERENT commitment on
every run — useless for anchoring. Quantising to a coarser integer grid
absorbs that noise while remaining specific enough to distinguish different
people (see the measured separation in docs/architecture.md 2a: 0.765 same-person
vs a 0.074 non-match ceiling — comfortably wider than quantisation noise).
"""

from __future__ import annotations

import numpy as np

from pipeline.evidence.canonical import keccak256
from pipeline.face.types import Embedding

# 1000x scale keeps 3 decimal digits of the cosine-comparable float32
# vector, which is far finer than the noise floor implied by the measured
# separation above.
QUANTISE_SCALE = 1000


def quantise(vec: np.ndarray, scale: int = QUANTISE_SCALE) -> np.ndarray:
    return np.round(vec * scale).astype(np.int16)


def face_commitment(embedding: Embedding, salt: bytes) -> bytes:
    """keccak256(salt || quantised_embedding_bytes). Never hand the raw
    embedding to anything outside this function and canonical.py's caller
    boundary — chain/ and evidence/bundle.py must only ever see the output
    of this function, never an Embedding (docs/architecture.md 3 boundary table)."""
    if len(salt) < 16:
        raise ValueError("salt must be at least 16 bytes — see config.get_commitment_salt()")
    q = quantise(embedding.vec)
    return keccak256(salt + q.tobytes())


def face_commitment_hex(embedding: Embedding, salt: bytes) -> str:
    return "0x" + face_commitment(embedding, salt).hex()
