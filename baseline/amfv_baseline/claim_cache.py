from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def normalize_claim(claim: str) -> str:
    """Normalize claim text before hashing and lookup."""
    claim = claim.lower().strip()
    claim = re.sub(r"\s+", " ", claim)
    claim = claim.rstrip(".")
    return claim


def claim_hash(claim: str) -> str:
    """Create a stable hash for a normalized claim."""
    normalized = normalize_claim(claim)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def utc_now() -> str:
    """Return an ISO timestamp in UTC."""
    return datetime.now(UTC).isoformat()


class ClaimCache:
    """SQLite cache for previously verified medical claims."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS verified_claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_hash TEXT NOT NULL,
                    normalized_claim TEXT NOT NULL,
                    claim_text TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    score REAL NOT NULL,
                    lexical_overlap REAL,
                    evidence_json TEXT NOT NULL,
                    verification_json TEXT NOT NULL,
                    verifier_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(claim_hash, scope)
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_verified_claims_hash_scope
                ON verified_claims (claim_hash, scope)
                """
            )

    def get_verified_claim(
        self,
        claim: str,
        scope: str,
    ) -> dict[str, Any] | None:
        """Return a cached verified claim if it exists."""
        hash_value = claim_hash(claim)

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM verified_claims
                WHERE claim_hash = ? AND scope = ?
                """,
                (hash_value, scope),
            ).fetchone()

        if row is None:
            return None

        return {
            "claim": row["claim_text"],
            "claim_type": row["claim_type"],
            "scope": row["scope"],
            "evidence": json.loads(row["evidence_json"]),
            "verification": json.loads(row["verification_json"]),
            "cache": {
                "status": "hit",
                "cache_id": row["id"],
                "claim_hash": row["claim_hash"],
                "verifier_name": row["verifier_name"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        }

    def upsert_verified_claim(
        self,
        claim: str,
        claim_type: str,
        scope: str,
        evidence: list[dict[str, Any]],
        verification: dict[str, Any],
        verifier_name: str,
    ) -> dict[str, Any]:
        """Insert or update a verified claim."""
        now = utc_now()
        hash_value = claim_hash(claim)
        normalized = normalize_claim(claim)

        evidence_json = json.dumps(evidence, ensure_ascii=False)
        verification_json = json.dumps(verification, ensure_ascii=False)

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO verified_claims (
                    claim_hash,
                    normalized_claim,
                    claim_text,
                    claim_type,
                    scope,
                    verdict,
                    score,
                    lexical_overlap,
                    evidence_json,
                    verification_json,
                    verifier_name,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_hash, scope)
                DO UPDATE SET
                    claim_text = excluded.claim_text,
                    claim_type = excluded.claim_type,
                    verdict = excluded.verdict,
                    score = excluded.score,
                    lexical_overlap = excluded.lexical_overlap,
                    evidence_json = excluded.evidence_json,
                    verification_json = excluded.verification_json,
                    verifier_name = excluded.verifier_name,
                    updated_at = excluded.updated_at
                """,
                (
                    hash_value,
                    normalized,
                    claim,
                    claim_type,
                    scope,
                    verification["verdict"],
                    verification["score"],
                    verification.get("lexical_overlap"),
                    evidence_json,
                    verification_json,
                    verifier_name,
                    now,
                    now,
                ),
            )

        cached = self.get_verified_claim(claim=claim, scope=scope)

        if cached is None:
            raise RuntimeError("Failed to read claim after writing to cache.")

        return cached