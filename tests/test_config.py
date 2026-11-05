import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(**overrides) -> Settings:
    # 로컬 .env 와 실행 환경 변수의 영향을 받지 않도록 값을 직접 지정
    base = {"EMBEDDING_PROVIDER": "local", "LLM_PROVIDER": "mock", "OPENAI_API_KEY": ""}
    return Settings(_env_file=None, **{**base, **overrides})


def test_local_and_mock_do_not_need_openai_key():
    settings = make_settings()
    assert settings.VECTOR_DIM == 512


@pytest.mark.parametrize("overrides", [
    {"EMBEDDING_PROVIDER": "openai"},
    {"LLM_PROVIDER": "openai"},
    {"EMBEDDING_PROVIDER": "openai", "LLM_PROVIDER": "openai", "OPENAI_API_KEY": "   "},
])
def test_openai_mode_without_key_fails_at_startup(overrides):
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        make_settings(**overrides)


def test_openai_mode_with_key_passes():
    settings = make_settings(EMBEDDING_PROVIDER="openai", LLM_PROVIDER="openai", OPENAI_API_KEY="test-key")
    assert settings.VECTOR_DIM == 1536
