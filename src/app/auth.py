"""Login seam — deliberately minimal (open question Q12 in the build plan).

`verify_credentials` is the single function the UI calls; swap this module's
internals for SSO / LDAP / OAuth later and nothing else in the app changes.

Credential sources, in priority order:
  1. data/users.json            — {"username": "<sha256 hex of password>", ...}
                                  (data/ is gitignored, like the HBUS tables)
  2. NIRE_USER + NIRE_PASSWORD  — environment variables (single user)
  3. demo fallback              — analyst / riskdemo, ONLY when neither of the
                                  above exists; a warning is printed so this
                                  can never silently reach production

Passwords are never stored or compared in plain text — sha256 only. This is
NOT production-grade auth (no salting, no rate limiting, no sessions beyond
the browser tab); it is the documented placeholder the plan calls for.
"""
import hashlib
import json
import os
import sys

from .. import config

_DEMO_USER = "analyst"
_DEMO_PASSWORD_HASH = hashlib.sha256(b"riskdemo").hexdigest()


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def load_users() -> dict:
    """Return {username: sha256_password_hash} from the best available source."""
    users_file = config.DATA_DIR / "users.json"
    if users_file.exists():
        with open(users_file) as fh:
            return json.load(fh)
    env_user = os.environ.get("NIRE_USER")
    env_password = os.environ.get("NIRE_PASSWORD")
    if env_user and env_password:
        return {env_user: _hash(env_password)}
    print("WARNING: no data/users.json and no NIRE_USER/NIRE_PASSWORD set — "
          "using the demo login (%s). Do not deploy like this." % _DEMO_USER,
          file=sys.stderr)
    return {_DEMO_USER: _DEMO_PASSWORD_HASH}


def demo_active() -> bool:
    """True when the demo fallback is the ONLY credential source — the
    login screen shows the demo credentials instead of stranding teammates
    in front of a password box with no password."""
    users_file = config.DATA_DIR / "users.json"
    return not users_file.exists() and not (
        os.environ.get("NIRE_USER") and os.environ.get("NIRE_PASSWORD"))


def verify_credentials(username, password) -> bool:
    """True iff the username exists and the password hash matches."""
    if not username or not password:
        return False
    users = load_users()
    expected = users.get(str(username).strip())
    return expected is not None and expected == _hash(str(password))
