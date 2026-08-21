from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import openai

from app.core.config import settings


class LLMService:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.client = openai.AsyncOpenAI(
            api_key=api_key or settings.OPENAI_API_KEY,
            base_url=base_url or settings.OPENAI_BASE_URL,
        )
        self.default_model = settings.DEFAULT_MODEL

    @staticmethod
    def _normalize_model(model: str) -> str:
        if model in {"deepseek/deepseek-v4-pro", "deepseek-v4-pro"}:
            return "deepseek-v4-pro"
        return model

    def _client_for_model(self, model: str):
        normalized_model = self._normalize_model(model)
        return self.client, normalized_model

    @staticmethod
    def _get_delta_extra_text(delta: Any, *field_names: str) -> str:
        for field_name in field_names:
            value = getattr(delta, field_name, None)
            if value:
                return value
        model_extra = getattr(delta, "model_extra", None) or {}
        for field_name in field_names:
            value = model_extra.get(field_name)
            if value:
                return value
        return ""

    @staticmethod
    def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
        if not usage:
            return None

        def read(obj: Any, key: str, default: int = 0) -> int:
            if obj is None:
                return default
            if isinstance(obj, dict):
                return int(obj.get(key) or default)
            return int(getattr(obj, key, default) or default)

        details = getattr(usage, "completion_tokens_details", None)
        if isinstance(usage, dict):
            details = usage.get("completion_tokens_details") or details
        prompt_tokens = read(usage, "prompt_tokens")
        completion_tokens = read(usage, "completion_tokens")
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "reasoning_tokens": read(details, "reasoning_tokens"),
            "total_tokens": read(
                usage,
                "total_tokens",
                prompt_tokens + completion_tokens,
            ),
        }

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
    ) -> AsyncGenerator[dict[str, Any], None]:
        if tool_definitions is None:
            raise ValueError("tool_definitions are required for report execution")
        try:
            selected_model = self._normalize_model(model or self.default_model)
            client, provider_model = self._client_for_model(selected_model)
            create_kwargs = {
                "model": provider_model,
                "messages": messages,
                "tools": tool_definitions,
                "tool_choice": tool_choice,
                "stream": True,
            }
            try:
                stream = await client.chat.completions.create(
                    **create_kwargs,
                    stream_options={"include_usage": True},
                )
            except Exception as exc:
                message = str(exc)
                if "stream_options" not in message and "include_usage" not in message:
                    raise
                stream = await client.chat.completions.create(**create_kwargs)

            tool_calls: list[dict[str, Any]] = []
            content = ""
            reasoning_content = ""
            usage_summary = None
            async for chunk in stream:
                chunk_usage = self._usage_to_dict(getattr(chunk, "usage", None))
                if chunk_usage:
                    usage_summary = chunk_usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning_delta = (
                    self._get_delta_extra_text(
                        delta,
                        "reasoning_content",
                        "thinking",
                    )
                    if delta
                    else ""
                )
                if reasoning_delta:
                    reasoning_content += reasoning_delta
                    yield {"type": "reasoning", "content": reasoning_delta}
                if delta and delta.content:
                    content += delta.content
                    yield {"type": "delta", "content": delta.content}
                if delta and delta.tool_calls:
                    for value in delta.tool_calls:
                        index = value.index if value.index is not None else 0
                        while index >= len(tool_calls):
                            tool_calls.append(
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            )
                        if value.id:
                            tool_calls[index]["id"] = value.id
                        if value.function.name:
                            tool_calls[index]["function"]["name"] = value.function.name
                        if value.function.arguments:
                            tool_calls[index]["function"]["arguments"] += (
                                value.function.arguments
                            )

            valid_tool_calls: list[dict[str, Any]] = []
            for index, tool_call in enumerate(tool_calls):
                if not tool_call["function"]["name"]:
                    continue
                tool_call["id"] = tool_call["id"] or f"tool_{index}"
                try:
                    json.loads(tool_call["function"]["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    yield {
                        "type": "error",
                        "content": (
                            "Incomplete tool arguments for "
                            f"{tool_call['function']['name']}: {exc}"
                        ),
                    }
                    return
                valid_tool_calls.append(tool_call)
                yield {"type": "tool_call", "tool_call": tool_call}
            yield {
                "type": "done",
                "content": content,
                "tool_calls": valid_tool_calls,
                "thinking": reasoning_content,
                "usage": usage_summary,
            }
        except Exception as exc:
            yield {"type": "error", "content": str(exc)}

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        try:
            selected_model = self._normalize_model(model or self.default_model)
            client, provider_model = self._client_for_model(selected_model)
            response = await client.chat.completions.create(
                model=provider_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as exc:
            return f"Error: {exc}"
