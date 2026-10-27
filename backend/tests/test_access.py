from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def client(password):
    return TestClient(create_app(Settings(access_password=password)))


def test_open_when_no_password():
    with client("") as c:
        assert c.get("/auth/status").json() == {"required": False, "authorized": True}
        assert c.get("/tools").status_code == 200


def test_gate_blocks_until_login():
    with client("s3cret") as c:
        assert c.get("/health").status_code == 200
        assert c.get("/tools").status_code == 401
        assert c.post("/auth/login", json={"password": "nope"}).status_code == 401
        assert c.get("/tools").status_code == 401
        assert c.post("/auth/login", json={"password": "s3cret"}).status_code == 200
        assert c.get("/tools").status_code == 200
        assert c.get("/auth/status").json()["authorized"] is True
        c.post("/auth/logout")
        assert c.get("/tools").status_code == 401
