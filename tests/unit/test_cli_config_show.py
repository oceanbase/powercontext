from powermem.cli.commands.config import _config_from_env_file
from powermem.cli.utils.output import format_output


def test_config_show_all_keeps_empty_optional_sections(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DATABASE_PROVIDER=oceanbase",
                "OCEANBASE_HOST=127.0.0.1",
                "LLM_PROVIDER=siliconflow",
                "LLM_MODEL=test-llm",
                "EMBEDDING_PROVIDER=qwen",
                "EMBEDDING_MODEL=text-embedding-v4",
            ]
        ),
        encoding="utf-8",
    )

    config = _config_from_env_file(str(env_file), section_filter=None)
    output = format_output(config, "config")

    assert "[AGENT (Optional)]" in output
    assert "[INTELLIGENT MEMORY (Optional)]" in output
    assert "[PERFORMANCE (Optional)]" in output
    assert "[SECURITY (Optional)]" in output
    assert "[TELEMETRY (Optional)]" in output
    assert "[AUDIT (Optional)]" in output
    assert "[LOGGING (Optional)]" in output
    assert "[SPARSE EMBEDDING (Optional)]" in output
    assert "[QUERY REWRITE (Optional)]" in output
