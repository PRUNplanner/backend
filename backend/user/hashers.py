from django.contrib.auth import hashers
from django.utils.encoding import force_bytes


class BCryptPasswordHasher(hashers.BCryptPasswordHasher):
    """
    Verifies existing `bcrypt$` hashes; check_password rehashes them to bcrypt_sha256 on login.
    bcrypt 5 raises for passwords over 72 bytes instead of truncating, so no `bcrypt$` hash of
    such a password can exist: refuse it rather than crash (truncating would accept any password
    that starts with the real one).
    """

    def verify(self, password: str, encoded: str) -> bool:
        if len(force_bytes(password)) > 72:
            return False
        return super().verify(password, encoded)
