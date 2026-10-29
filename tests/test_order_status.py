"""Store/admin order-status updates (these routes used to shadow the service and 500)."""
import os

os.environ.setdefault("SECRET_KEY", "test-secret")

from types import SimpleNamespace

from fastapi.testclient import TestClient


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload, self.one = db, table, [], "select", None, False

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def eq(self, col, val): self.filters.append((col, val)); return self
    def single(self): self.one = True; return self
    def update(self, payload): self.op, self.payload = "update", payload; return self
    def insert(self, payload): self.op, self.payload = "insert", payload; return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self.op == "insert":
            rows.extend(self.payload if isinstance(self.payload, list) else [self.payload])
            return SimpleNamespace(data=[self.payload])
        hit = [r for r in rows if all(r.get(c) == v for c, v in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        if self.one:
            return SimpleNamespace(data=hit[0] if hit else None)
        return SimpleNamespace(data=hit)


class FakeSupabase:
    def __init__(self, db): self.db = db
    def table(self, name): return FakeQuery(self.db, name)


def make_client(monkeypatch, db):
    from app.main import app
    from app.api import artisan, admin, deps
    fake = FakeSupabase(db)
    for mod in (artisan, admin, deps):
        monkeypatch.setattr(mod, "get_supabase", lambda: fake)
    return TestClient(app)


def auth(**claims):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(claims)}"}


def seed():
    return {
        "users_profile": [{"id": "u-art", "is_suspended": False}, {"id": "u-admin", "is_suspended": False},
                          {"id": "u-banned", "is_suspended": True}],
        "artisans": [{"id": 7, "name": "กลุ่มทอผ้าบ้านนาข่า"}],
        "orders": [{"id": 1, "status": "payment_pending_review", "products": {"artisan_id": 7}}],
    }


def test_store_moves_order_forward_and_logs_event(monkeypatch):
    db = seed()
    client = make_client(monkeypatch, db)
    res = client.put("/api/artisan/orders/1/status", json={"status": "paid"},
                     headers=auth(sub="u-art", role="artisan", artisan_id=7))
    assert res.status_code == 200, res.text
    assert db["orders"][0]["status"] == "paid"
    assert db["order_status_events"][0]["to_status"] == "paid"


def test_invalid_transition_is_409(monkeypatch):
    client = make_client(monkeypatch, seed())
    res = client.put("/api/admin/orders/1/status", json={"status": "delivered"},
                     headers=auth(sub="u-admin", role="admin"))
    assert res.status_code == 409


def test_other_store_is_forbidden(monkeypatch):
    client = make_client(monkeypatch, seed())
    res = client.put("/api/artisan/orders/1/status", json={"status": "paid"},
                     headers=auth(sub="u-art", role="artisan", artisan_id=99))
    assert res.status_code == 403


def test_suspended_user_token_is_rejected(monkeypatch):
    client = make_client(monkeypatch, seed())
    res = client.put("/api/admin/orders/1/status", json={"status": "paid"},
                     headers=auth(sub="u-banned", role="admin"))
    assert res.status_code == 403
