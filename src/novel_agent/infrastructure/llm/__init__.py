"""LLM infrastructure package."""

from novel_agent.infrastructure.llm.capabilities import ProbeReport, probe_model
from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.llm.ollama_adapter import ChatResult, OllamaAdapter

__all__ = [
    "ChatResult",
    "LLMGateway",
    "OllamaAdapter",
    "ProbeReport",
    "probe_model",
]
