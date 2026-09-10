"""Each test session gets a throwaway database and a known admin password."""
import os
import shutil
import tempfile

# A fresh DB dir per pytest run, so SEED_ADMIN_PASSWORD actually applies.
_TMP = tempfile.mkdtemp(prefix="science-english-test-")
os.environ["SCIENCE_ENGLISH_DATA"] = _TMP
os.environ["SEED_ADMIN_PASSWORD"] = "test-admin-pw"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
