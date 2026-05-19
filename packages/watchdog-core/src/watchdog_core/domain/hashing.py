"""Stable hashing utilities for dedupe-key derivation.

`compute_message_hash` is shared by:
  * the persistence layer (stored in the `message_hash` column of
    `log_events` so the dedupe index can hit fast); and
  * the ingestion service (computes the lookup key before asking the
    repository whether a duplicate exists).

Both call sites MUST produce the same digest for the same input, hence
this single shared helper.
"""

from __future__ import annotations

from hashlib import blake2b

_DIGEST_BYTES = 16


def compute_message_hash(message: str) -> str:
    """Hex-encoded 16-byte BLAKE2b digest of `message`."""
    return blake2b(message.encode("utf-8"), digest_size=_DIGEST_BYTES).hexdigest()
