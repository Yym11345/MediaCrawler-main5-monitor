def test_resolve_uvicorn_bind_defaults_to_lan_host(monkeypatch):
    monkeypatch.delenv("API_HOST", raising=False)
    monkeypatch.delenv("API_PORT", raising=False)

    from api.main import resolve_uvicorn_bind

    assert resolve_uvicorn_bind() == ("0.0.0.0", 8080)


def test_resolve_uvicorn_bind_respects_env(monkeypatch):
    monkeypatch.setenv("API_HOST", "192.168.1.20")
    monkeypatch.setenv("API_PORT", "8090")

    from api.main import resolve_uvicorn_bind

    assert resolve_uvicorn_bind() == ("192.168.1.20", 8090)
