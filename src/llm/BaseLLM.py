from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.models.responses.OutgoingResponsePayload import MCPToolDiagnosisResponse
from src.mcp_interface.agent_client import MCPTools
from src.models.AgentContext import AgentContext
from typing import Any

@dataclass
class LLMResult:
    responses: list[MCPToolDiagnosisResponse]
    reply: str | None  # the model's final answer to the officer, in plain English
    failures: list[str] = field(default_factory=list)  # tool calls that failed: "tool: error"


class BaseLLM(ABC):

    def __init__(self, context: AgentContext):
        pass

    @abstractmethod
    async def process(self) -> LLMResult:
        pass

    @abstractmethod
    def convert_mcptools(self, mcp_tools: list[MCPTools]) -> list[Any]:
        pass