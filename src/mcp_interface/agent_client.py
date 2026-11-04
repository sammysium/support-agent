from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp import stdio_client
from dataclasses import dataclass
from typing import Any
from pathlib import Path
from pydantic import TypeAdapter

from src.models.responses.OutgoingResponsePayload import MCPToolDiagnosisResponse




backend_dir = Path(__file__).resolve().parents[2]

TOOL_CALL_TIMEOUT_SECONDS = 30.0


server = StdioServerParameters(
    command="uv",
    args=["run", "python", "-m", "src.mcp_interface.agent_server"],
    cwd=str(backend_dir),
)

@dataclass
class MCPTools:
    name: str
    description: str
    input_schema: Any


class ToolCallError(Exception):
    """The tool ran but failed (bad args, unknown tool, or the tool raised).
    The message is what the server reported, safe to hand back to the model."""


responses_adapter = TypeAdapter(list[MCPToolDiagnosisResponse])


class MCPClient:
    """
    One long-lived connection to the MCP server subprocess, opened once in
    FastAPI's lifespan and shared by every request.

    Opening a stdio_client per call meant spawning `uv run python -m ...`
    for every single tool call: a cold start, a fresh Firebase init and a
    fresh Couchbase connection each time. ClientSession multiplexes
    concurrent requests over the one pipe by request id, so sharing it
    across requests is safe.
    """

    def __init__(self):
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def __aenter__(self) -> "MCPClient":
        read, write = await self._stack.enter_async_context(stdio_client(server))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._stack.aclose()

    async def list_tools(self) -> list[MCPTools]:
        result = await self.session.list_tools()
        return [
            MCPTools(name=tool.name, description=tool.description, input_schema=tool.input_schema)
            for tool in result.tools
        ]

    async def call_tool(self, name: str, args: dict) -> list[MCPToolDiagnosisResponse]:
        result = await self.session.call_tool(name, args, read_timeout_seconds=TOOL_CALL_TIMEOUT_SECONDS)
        if result.is_error:
            message = " ".join(c.text for c in result.content if getattr(c, "text", None))
            raise ToolCallError(message or f"Tool {name} failed")
        return responses_adapter.validate_python((result.structured_content or {}).get("result", []))
