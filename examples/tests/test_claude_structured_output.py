"""Structured output test for the Claude Agent SDK adapter."""

from __future__ import annotations

import pytest

from headless_coder_sdk.core import create_coder

from .env import claude_credentials_available, claude_sdk_available, python_supports_claude
from .workspace import ensure_claude_config

CLAUDE_READY = claude_sdk_available() and claude_credentials_available()
py_version_reason = 'Claude Agent SDK requires Python 3.10+.'
missing_sdk_reason = 'claude-agent-sdk is not installed; install it to run Claude tests.'
missing_creds_reason = 'Claude credentials are unavailable in the environment.'
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not python_supports_claude(), reason=py_version_reason),
    pytest.mark.skipif(not claude_sdk_available(), reason=missing_sdk_reason),
    pytest.mark.skipif(not claude_credentials_available(), reason=missing_creds_reason),
]

if claude_sdk_available():
    from headless_coder_sdk.claude_agent_sdk import CODER_NAME as CLAUDE_NAME
else:  # pragma: no cover - handled by skip
    CLAUDE_NAME = 'claude'

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
        },
    },
    "required": ["summary", "recommendations"],
}


async def test_claude_structured_output(workspace_factory) -> None:
    """Ensures Claude outputs JSON adhering to the provided schema."""

    workspace = workspace_factory('claude_structured')
    ensure_claude_config(workspace)

    coder = create_coder(
        CLAUDE_NAME,
        {
            "workingDirectory": str(workspace),
            "permissionMode": 'bypassPermissions',
            "allowedTools": ['Write', 'Edit', 'Read', 'NotebookEdit'],
        },
    )

    thread = await coder.start_thread()
    try:
        result = await thread.run(
            'Summarise this repository and propose two follow-up tasks.',
            {"outputSchema": SCHEMA},
        )
    finally:
        close = getattr(coder, "close", None)
        if callable(close):
            await close(thread)

    assert isinstance(result.json, dict)
    assert isinstance(result.json.get('summary'), str)
    recommendations = result.json.get('recommendations')
    assert isinstance(recommendations, list) and len(recommendations) >= 2
