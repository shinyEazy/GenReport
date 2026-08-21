import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.llm_service import LLMService


class LLMServiceCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.llm_service.openai.AsyncOpenAI")
    async def test_constructor_uses_per_run_provider_settings(self, client_factory) -> None:
        LLMService(
            api_key="config-key",
            base_url="https://provider.example/v1",
        )

        client_factory.assert_called_once_with(
            api_key="config-key",
            base_url="https://provider.example/v1",
        )

    async def test_stream_chat_requires_stateless_tool_definitions(self) -> None:
        service = object.__new__(LLMService)
        service.default_model = "test-model"

        with self.assertRaisesRegex(ValueError, "tool_definitions"):
            await anext(
                service.stream_chat(
                    [{"role": "user", "content": "Create a report"}],
                )
            )

    async def test_chat_uses_async_openai_completion_contract(self) -> None:
        service = object.__new__(LLMService)
        completion = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Vietnamese report ready")
                    )
                ]
            )
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=completion),
            )
        )
        service.client = client
        service.default_model = "test-model"

        result = await service.chat(
            [{"role": "user", "content": "Create a report"}],
            temperature=0.2,
            max_tokens=256,
        )

        self.assertEqual(result, "Vietnamese report ready")
        completion.assert_awaited_once_with(
            model="test-model",
            messages=[{"role": "user", "content": "Create a report"}],
            temperature=0.2,
            max_tokens=256,
        )


if __name__ == "__main__":
    unittest.main()
