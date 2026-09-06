"""Face detection, alignment, embedding, liveness.

Boundary (docs/architecture.md 3): knows numpy arrays and ONNX sessions only.
Must not know about search providers, HTTP, chains, or evidence bundles.
"""
