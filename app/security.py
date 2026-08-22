"""
Password and access-code hashing.

Uses PBKDF2-HMAC-SHA256 from the standard library so there's no extra
dependency to install at the venue. Stored format:

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

If you later add bcrypt/argon2, keep this format prefix so old rows can be
detected and upgraded rather than silently failing to verify.

Why hash a parent access code at all: the parent portal is the one place in
Souly holding a child's progress data behind a shared secret. Storing that
secret in plaintext would mean anyone with read access to souly.db — which
travels on a laptop to a competition — can open any parent's account.
"""

import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 120_000
_SALT_BYTES = 16


def hash_secret(raw: str, *, iterations: int = _ITERATIONS) -> str:
    """Hash a password or access code for storage."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_secret(raw: str, stored: str) -> bool:
    """Constant-time check of a supplied secret against a stored hash."""
    try:
        algorithm, iterations_s, salt_hex, digest_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", raw.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations_s)
        )
    except (ValueError, AttributeError):
        return False
    # compare_digest, not ==, so response time doesn't leak how much matched.
    return hmac.compare_digest(digest.hex(), digest_hex)


def generate_access_code(words: int = 3) -> str:
    """
    Generate a parent access code that a human can read over the phone.

    Format: SOULY-XXXX-XXXX-XXXX using an alphabet with no 0/O or 1/I/L,
    because those get misheard and mistyped constantly.
    """
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(words)]
    return "SOULY-" + "-".join(groups)
