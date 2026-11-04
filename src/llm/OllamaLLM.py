import re
from typing import Callable

from ollama import AsyncClient

from src.utils.log import log_me
from src.models.responses.OutgoingResponsePayload import MCPToolDiagnosisResponse
from src.llm.BaseLLM import BaseLLM, LLMResult
from src.models.AgentContext import AgentContext
from src.mcp_interface.agent_client import MCPTools, ToolCallError, responses_adapter

INJECTED = {"request", "server_config", "formula_config"}

SYSTEM_PROMPT = (
    "You help field officers who use the FieldSmart app. Use the tools to find out what went wrong with the "
    "problem they report. When you are done, answer the officer in plain English, in two or three short sentences: "
    "what went wrong and what they should do, if anything. Build the answer from the tools' officer_message fields. "
    "Never mention internal systems or technical terms: no Couchbase, Sync Gateway, Firebase, Remote Config, "
    "Fineract, formula, channel, revision, field names or ids. If a tool fails or doesn't explain the problem, "
    "don't guess at causes: say you couldn't find what went wrong and that it has been passed to the team."
)
THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)
MAX_TURNS = 8
# Ollama's default context window is a few thousand tokens and it silently drops the start of the
# prompt when that overflows. The system prompt, tool definitions (feature keys, document types,
# relationships) and up to MAX_TURNS of tool results need far more than that.
NUM_CTX = 16384


def inline_refs(value, defs: dict):
    if isinstance(value, dict):
        ref = value.get("$ref", "")
        if ref.startswith("#/$defs/") and ref[len("#/$defs/"):] in defs:
            return inline_refs({**defs[ref[len("#/$defs/"):]], **{k: v for k, v in value.items() if k != "$ref"}}, defs)
        return {k: inline_refs(v, defs) for k, v in value.items()}
    if isinstance(value, list):
        return [inline_refs(v, defs) for v in value]
    return value


class OllamaLLM(BaseLLM):

    def __init__(
        self,
        context: AgentContext,
        history: list[dict] | None = None,
        message: str | None = None,
        on_tool_call: Callable[[str], None] | None = None,
    ):
        """history: the chat's earlier turns ({"role": "user"|"assistant", "content"}), so a follow-up like
        "it's the one from last week" makes sense. message: this turn (default: the opening report).
        on_tool_call: told the name of each tool before it runs (the officer sees what's being checked)."""
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *(history or []),
            {"role": "user", "content": message or context.request.issue},
        ]
        self.llm_model = "qwen3:8b"
        self.client = AsyncClient()
        self.context = context
        self.on_tool_call = on_tool_call

    def convert_mcptools(self, mcp_tools: list[MCPTools]) -> list[dict]:
        tools = []
        for t in mcp_tools:
            schema = dict(t.input_schema)
            schema["properties"] = {k: v for k, v in schema.get("properties", {}).items() if k not in INJECTED}
            schema["required"] = [r for r in schema.get("required", []) if r not in INJECTED]
            # inline "#/$defs/X" references (e.g. enums like EntityType) so the model sees the allowed values
            defs = schema.pop("$defs", {})
            schema["properties"] = inline_refs(schema["properties"], defs)
            tools.append({"type": "function", "function": {"name": t.name, "description": t.description, "parameters": schema}})
        return tools
    
    async def process(self) -> LLMResult:
        tools = self.convert_mcptools(self.context.mcp_tools)
        collected: list[MCPToolDiagnosisResponse] = []
        failures: list[str] = []

        for _ in range(MAX_TURNS):
            response = await self.client.chat(
                model=self.llm_model,
                messages=self.messages,
                tools=tools,
                options={"num_ctx": NUM_CTX},
            )

            # no tool calls means the model is done: what it says now is its answer to the officer
            if not response.message.tool_calls:
                reply = THINKING.sub("", response.message.content or "").strip()
                return LLMResult(responses=collected, reply=reply or None, failures=failures)

            self.messages.append(response.message)

            for tool in response.message.tool_calls:
                function_name = tool.function.name
                function_args = {**tool.function.arguments, **self.context.tool_args()}
                if self.on_tool_call:
                    self.on_tool_call(function_name)
                log_me(f"-> Calling {function_name} with {tool.function.arguments}")

                try:
                    tool_responses = await self.context.mcp_client.call_tool(function_name, function_args)
                    collected.extend(tool_responses)
                    content = responses_adapter.dump_json(tool_responses).decode()
                except ToolCallError as e:
                    # tell the model what went wrong so it can retry with better args or pick another tool
                    log_me(f"-> {function_name} failed: {e}")
                    failures.append(f"{function_name}: {e}")
                    content = f"ERROR: {e}"

                self.messages.append({
                    "role": "tool",
                    "content": content,
                    "tool_name": function_name,
                })

        return LLMResult(responses=collected, reply=None, failures=failures)