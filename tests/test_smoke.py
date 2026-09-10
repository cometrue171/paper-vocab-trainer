"""End-to-end smoke tests: init -> login -> admin -> isolation."""
import os

os.environ.setdefault("SEED_ADMIN_PASSWORD", "test-admin-pw")

from app import create_app  # noqa: E402


def _client():
    return create_app().test_client()


def test_api_requires_login():
    c = _client()
    assert c.get("/api/summary").status_code == 401
    assert c.get("/api/words").status_code == 401


def test_admin_login_and_create_account():
    c = _client()
    r = c.post("/api/auth/login",
               json={"username": "admin", "password": "test-admin-pw"})
    assert r.status_code == 200 and r.get_json()["is_admin"] is True

    r = c.post("/api/admin/users",
               json={"username": "tester", "password": "pass1234",
                     "direction": "General / 综合"})
    assert r.status_code in (200, 400)   # 400 if it already exists

    u = _client()
    assert u.post("/api/auth/login",
                  json={"username": "tester", "password": "pass1234"}).status_code == 200
    summary = u.get("/api/summary").get_json()
    assert summary["pool_remaining"] == 0          # fresh, isolated pool
    assert u.get("/api/words").get_json()["total"] == 0


def test_normal_account_cannot_open_admin():
    u = _client()
    u.post("/api/auth/login", json={"username": "tester", "password": "pass1234"})
    assert u.get("/admin.html").status_code == 403
    assert u.get("/api/admin/users").status_code == 403
