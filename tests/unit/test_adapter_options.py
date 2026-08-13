"""Tests for adapter options including repeat_penalty."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from novel_agent.infrastructure.llm.ollama_adapter import OllamaAdapter
from novel_agent.settings import Settings


class MockOllamaClient:
    def __init__(self):
        self.called_with: dict = {}

    def list(self):
        return MagicMock(models=[])

    def chat(self, **kwargs: dict) -> MagicMock:
        self.called_with = kwargs
        return MagicMock(
            message=MagicMock(
                content='{"title": "test", "content": "test content", "scene_summary": "test"}',
                thinking=None,
            ),
            model_dump=dict,
        )


def test_adapter_chat_includes_repeat_penalty() -> None:
    """Test that chat includes repeat_penalty in options."""
    settings = Settings(
        ollama_model="test-model",
        context_limit=4096,
        default_num_predict=512,
        repeat_penalty=1.15,
    )

    with patch("ollama.Client", return_value=MockOllamaClient()):
        adapter = OllamaAdapter(settings=settings)
        adapter.chat([{"role": "user", "content": "test"}])

    assert adapter.client.called_with is not None
    options = adapter.client.called_with.get("options", {})
    assert "repeat_penalty" in options
    assert options["repeat_penalty"] == 1.15


def test_adapter_chat_stream_includes_repeat_penalty() -> None:
    """Test that chat_stream includes repeat_penalty in options."""
    settings = Settings(
        ollama_model="test-model",
        context_limit=4096,
        default_num_predict=512,
        repeat_penalty=1.15,
    )

    mock_client = MagicMock()
    mock_stream = iter([
        MagicMock(message=MagicMock(content="chunk")),
    ])
    mock_client.chat.return_value = mock_stream

    with patch("ollama.Client", return_value=mock_client):
        adapter = OllamaAdapter(settings=settings)
        list(adapter.chat_stream([{"role": "user", "content": "test"}]))

    call_kwargs = mock_client.chat.call_args[1]
    options = call_kwargs.get("options", {})
    assert "repeat_penalty" in options
    assert options["repeat_penalty"] == 1.15


def test_adapter_respects_custom_repeat_penalty() -> None:
    """Test that custom repeat_penalty in settings is used."""
    for rp in [1.1, 1.15, 1.2, 1.3]:
        settings = Settings(
            ollama_model="test-model",
            context_limit=4096,
            default_num_predict=512,
            repeat_penalty=rp,
        )

        with patch("ollama.Client", return_value=MockOllamaClient()):
            adapter = OllamaAdapter(settings=settings)
            adapter.chat([{"role": "user", "content": "test"}])

        options = adapter.client.called_with.get("options", {})
        assert options["repeat_penalty"] == rp, f"Expected {rp}, got {options.get('repeat_penalty')}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])