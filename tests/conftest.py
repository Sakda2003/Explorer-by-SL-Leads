"""Point the whole test session at a throwaway database before `backend` is imported.

Without this, `LEADLENS_DATA_DIR` is unset, so `core.DB_PATH` and `auth._db_path()` both
resolve to the real `data/leadlens.db` -- the live database with ~3,000 customer records in it.
`tests/test_pipeline.py` and `test_backup.py` already build their own temp directories, but
`test_auth.py` does not: it calls `verify()`, which opens `app_users` on whatever
`_db_path()` returns and, on the `/api/auth/me` path, *writes* `last_login_at` and an audit row.

This is not hypothetical. On 2026-08-20 an ad-hoc `TestClient` check run with
`BASIC_AUTH_USER=demo` created a live, active **admin** account in the production database --
`ensure_basic_user()` did exactly what it is supposed to, against exactly the wrong file. The
row had to be deleted by hand, and the only reason it was noticed at all is that it then broke
an unrelated assertion in `test_auth.py`.

Setting the environment here rather than in each test is deliberate: it has to be in force
before `backend.core` is imported, because that module resolves `DB_PATH` at import time.
pytest imports `conftest.py` ahead of any test module, which is the only hook early enough.
Tests that want their own database still override `core.DB_PATH` directly and are unaffected.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Created before any `from backend import ...` runs anywhere in the session, and deliberately
# not cleaned up per-test: several suites reuse the path across test methods.
_SESSION_DATA_DIR = tempfile.mkdtemp(prefix="leadlens-tests-")

os.environ["LEADLENS_DATA_DIR"] = _SESSION_DATA_DIR
os.environ["LEADLENS_DB_PATH"] = str(Path(_SESSION_DATA_DIR) / "leadlens-test.db")

# Nothing in the suite should be exercising a configured gate by accident; each auth test sets
# the mode it wants on `auth.config` explicitly.
for _leaked in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "LEADLENS_REQUIRE_AUTH",
                "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "LEADLENS_TAILSCALE_AUTH"):
    os.environ.pop(_leaked, None)


@pytest.fixture(scope="session", autouse=True)
def guard_production_database():
    """Fail loudly if anything has re-pointed the suite at the real database mid-run."""
    from backend import auth, core

    real = (Path(__file__).resolve().parents[1] / "data" / "leadlens.db").resolve()
    for label, resolved in (("core.DB_PATH", Path(core.DB_PATH).resolve()),
                            ("auth._db_path()", Path(auth._db_path()).resolve())):
        assert resolved != real, f"{label} points at the production database ({real})"
    yield
