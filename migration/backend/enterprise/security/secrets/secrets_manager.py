"""
Secrets Manager
File: migration/backend/enterprise/security/secrets/secrets_manager.py

Stores any sensitive value that isn't a DB password:
  - Webhook signing secrets
  - SMTP passwords
  - Slack tokens
  - Custom encryption keys
  - Third-party API keys

Uses the same AES-256 Fernet encryption as connection_manager.
Values are NEVER returned via API — only written and deleted.
To verify a secret is set, check the key exists (GET /secrets).

Router endpoints:
    POST   /secrets/{key}     → store/update a secret
    GET    /secrets            → list key names (no values)
    DELETE /secrets/{key}      → delete a secret
    POST   /secrets/{key}/verify → verify a value matches stored secret
"""

import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.enterprise.connection_manager.connection_manager import encrypt_password, decrypt_password
from backend.shared.config.logging import logger


class SecretsManager:

    def set_secret(
        self,
        db:           Session,
        tenant_id:    str,
        key_name:     str,
        value:        str,
        description:  str = None,
        created_by:   str = None,
    ) -> dict:
        """Store or update a secret. Value is AES-256 encrypted before storage."""
        encrypted = encrypt_password(value)
        now = datetime.datetime.utcnow()

        db.execute(
            text("""
                INSERT INTO secrets_vault
                    (id, tenant_id, key_name, encrypted_value,
                     description, created_by_id, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), :tid, :key, :val,
                     :desc, :by, :now, :now)
                ON CONFLICT (tenant_id, key_name)
                DO UPDATE SET
                    encrypted_value = :val,
                    description     = :desc,
                    updated_at      = :now
            """),
            {
                "tid":  tenant_id,
                "key":  key_name,
                "val":  encrypted,
                "desc": description,
                "by":   created_by,
                "now":  now,
            }
        )
        db.commit()
        logger.info("Secret stored", tenant_id=tenant_id, key=key_name)
        return {"key_name": key_name, "status": "stored", "tenant_id": tenant_id}

    def get_secret_value(self, db: Session, tenant_id: str, key_name: str) -> Optional[str]:
        """
        Retrieve and decrypt a secret value.
        INTERNAL USE ONLY — never expose via API.
        """
        row = db.execute(
            text("SELECT encrypted_value FROM secrets_vault WHERE tenant_id=:tid AND key_name=:key"),
            {"tid": tenant_id, "key": key_name}
        ).fetchone()
        if not row:
            return None
        return decrypt_password(row[0])

    def list_keys(self, db: Session, tenant_id: str) -> List[dict]:
        """List secret key names (without values) for a tenant."""
        rows = db.execute(
            text("""
                SELECT key_name, description, created_at, updated_at
                FROM secrets_vault
                WHERE tenant_id=:tid
                ORDER BY key_name
            """),
            {"tid": tenant_id}
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row._mapping)
            for k, v in d.items():
                if hasattr(v, "isoformat"): d[k] = v.isoformat()
            result.append(d)
        return result

    def delete_secret(self, db: Session, tenant_id: str, key_name: str) -> dict:
        db.execute(
            text("DELETE FROM secrets_vault WHERE tenant_id=:tid AND key_name=:key"),
            {"tid": tenant_id, "key": key_name}
        )
        db.commit()
        logger.info("Secret deleted", tenant_id=tenant_id, key=key_name)
        return {"key_name": key_name, "status": "deleted"}

    def verify_secret(self, db: Session, tenant_id: str, key_name: str, test_value: str) -> bool:
        """Verify that test_value matches the stored secret without returning the secret."""
        stored = self.get_secret_value(db, tenant_id, key_name)
        if stored is None:
            return False
        return stored == test_value
