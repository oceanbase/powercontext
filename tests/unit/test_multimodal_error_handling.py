from unittest.mock import MagicMock, patch

from powermem import Memory
from powermem.utils.utils import parse_vision_messages


def test_parse_vision_messages_skips_failed_image_description():
    llm = MagicMock()
    llm.generate_response.side_effect = RuntimeError("Missing Content Length")

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Invalid image test"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.invalid/image.jpg"},
                },
            ],
        }
    ]

    assert parse_vision_messages(messages, llm=llm) == [
        {"role": "user", "content": "Invalid image test"}
    ]


@patch("powermem.core.memory.VectorStoreFactory")
@patch("powermem.core.memory.LLMFactory")
@patch("powermem.core.memory.EmbedderFactory")
def test_memory_add_returns_empty_results_when_only_image_processing_fails(
    mock_embedder_factory,
    mock_llm_factory,
    mock_vector_factory,
):
    mock_vector_factory.create.return_value = MagicMock()
    mock_embedder_factory.create.return_value = MagicMock()
    mock_llm = MagicMock()
    mock_llm.generate_response.side_effect = RuntimeError("Missing Content Length")
    mock_llm_factory.create.return_value = mock_llm

    memory = Memory()
    memory.config.setdefault("llm", {}).setdefault("config", {})[
        "enable_vision"
    ] = True

    result = memory.add(
        messages=[
            {
                "role": "user",
                "content": {
                    "type": "image_url",
                    "image_url": {"url": "https://example.invalid/image.jpg"},
                },
            }
        ]
    )

    assert result == {"results": []}
