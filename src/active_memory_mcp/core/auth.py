"""Authentication and authorization for ActiveMemory API.

API tokens with scope-based access control (read/write/admin).
"""

import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

logger = logging.getLogger(__name__)

VALID_SCOPES = {"read", "write", "admin"}
SCOPE_HIERARCHY = {"read": 1, "write": 2, "admin": 3}

_TOKEN_PREFIX = "am_"


def generate_token() -> tuple[str, str]:
    """Generate a new API token. Returns (raw_token, token_hash)."""
    raw = _TOKEN_PREFIX + secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


def hash_token(token: str) -> str:
    """Hash a raw token for storage/comparison."""
    return hashlib.sha256(token.encode()).hexdigest()


def validate_scopes(scopes_str: str, required_scope: str) -> bool:
    """Check if granted scopes satisfy the required scope level."""
    if not scopes_str:
        return False
    granted = {s.strip() for s in scopes_str.split(",") if s.strip()}
    if "admin" in granted:
        return True
    if required_scope == "admin":
        return False
    if "write" in granted and required_scope in ("read", "write"):
        return True
    if "read" in granted and required_scope == "read":
        return True
    return required_scope in granted


def get_encryption_key(master_key: Optional[str] = None) -> bytes:
    """Derive a 32-byte Fernet key from the master key or generate one."""
    if master_key:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"active_memory_v1",
            iterations=100000,
        )
        return base64.urlsafe_b64encode(kdf.derive(master_key.encode()))
    return Fernet.generate_key()


def encrypt_embedding(embedding_bytes: bytes, key: bytes) -> str:
    """Encrypt embedding bytes to Fernet token."""
    f = Fernet(key)
    return f.encrypt(embedding_bytes).decode()


def decrypt_embedding(encrypted_token: str, key: bytes) -> bytes:
    """Decrypt Fernet token back to embedding bytes."""
    f = Fernet(key)
    return f.decrypt(encrypted_token.encode())


def create_encryption_salt() -> str:
    """Generate a random salt for document-level encryption."""
    return secrets.token_hex(16)
