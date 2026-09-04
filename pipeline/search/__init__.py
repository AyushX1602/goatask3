"""Search providers and orchestration.

Boundary (architecture.md 3): knows Candidate, HTTP, provider APIs.
Must not know about face models, cosine scoring, or chains. A provider
never scores a face (R-03) — it returns candidates, nothing more.
"""
