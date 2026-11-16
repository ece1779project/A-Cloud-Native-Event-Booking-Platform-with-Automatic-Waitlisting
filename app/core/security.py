"""Security primitives: Argon2id password hashing, EdDSA access-token signing
and verification, random token generation and SHA-256 token digests.

Pure functions over their inputs; loading accounts is done by services.

Design: TECHNICAL_DESIGN.md Sections 9.1 and 9.2.
"""
