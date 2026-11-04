from app.config import settings
from app.utile import embedding


def test_warm_up_loads_model_in_local_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(embedding, "get_model", lambda: calls.append("load"))

    assert embedding.warm_up_model() is True
    assert calls == ["load"]


def test_warm_up_skips_in_openai_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(embedding, "get_model", lambda: calls.append("load"))

    assert embedding.warm_up_model() is False
    assert calls == []
