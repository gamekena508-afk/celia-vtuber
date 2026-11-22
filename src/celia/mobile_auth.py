"""Independent, fail-closed password/session boundary for Célia Mobile."""
from __future__ import annotations

import os
from .roteiro_auth import RoteiroAuth, verify_password

MOBILE_COOKIE = "celia_mobile_session"
MOBILE_PASSWORD_HASH_ENV = "CELIA_MOBILE_PASSWORD_HASH"


class MobileAuth(RoteiroAuth):
    @property
    def configured(self) -> bool:
        return os.getenv(MOBILE_PASSWORD_HASH_ENV, "").strip().startswith("scrypt$")

    def authenticate(self, password: str, client_ip: str) -> bool:
        if not self.login_allowed(client_ip).allowed:
            return False
        encoded = os.getenv(MOBILE_PASSWORD_HASH_ENV, "").strip()
        if not self.configured or not verify_password(password, encoded):
            self.record_failure(client_ip)
            return False
        self.clear_failures(client_ip)
        return True

    def valid_session(self, token: str | None) -> bool:
        return self.configured and super().valid_session(token)


mobile_auth = MobileAuth()
