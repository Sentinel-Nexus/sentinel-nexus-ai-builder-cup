from services.web.app import app


def test_health():
    resp = app.test_client().get("/")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "service": "marketsentinel"}
