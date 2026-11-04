from dataclasses import dataclass
from typing import Any

from src.models.IncomingRequestPayload import IncomingRequestPayload
from src.models.FormulaConfig import FormulaConfig
from src.mcp_interface.agent_client import MCPClient, MCPTools
from src.models.CountryConfig import CountryConfig


@dataclass
class AgentContext:
    request: IncomingRequestPayload
    server_config: CountryConfig
    formula_config: FormulaConfig | None  # None: the country has no formulas
    mcp_tools: list[MCPTools]
    mcp_client: MCPClient

    def tool_args(self) -> dict[str, Any]:
        """The arguments injected into every tool call (never shown to the model). Only what was received
        (exclude_unset): a setting the phone or Remote Config doesn't have must not arrive as its default, or the
        tools can't tell "off" from "this app version doesn't have it"."""
        return {
            "request": self.request.model_dump(mode="json", by_alias=True, exclude_unset=True),
            "server_config": self.server_config.model_dump(mode="json", by_alias=True, exclude_unset=True),
            "formula_config": self.formula_config.model_dump(mode="json", by_alias=True) if self.formula_config else None,
        }