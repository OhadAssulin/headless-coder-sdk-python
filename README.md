# python-headless-coder  
> Unified Python SDK for headless AI coders (Codex, Claude, Gemini)

**python-headless-coder** mirrors the TypeScript [headless-coder-sdk](https://github.com/OhadAssulin/headless-coder-sdk) but ships pure Python packages. It exposes the same adapter registry, thread handles, structured outputs, and streaming semantics so you can switch AI coder backends without rewriting your application.

---

## 🚀 Why use it?
- Avoid vendor lock-in by targeting Codex, Claude Agent SDK, and Gemini CLI through one API
- Consistent thread lifecycle (`start_thread`, `resume_thread`, `run`, `run_streamed`) across providers
- Structured output helpers and cancellation semantics identical to the TS version
- Works anywhere Python runs (virtualenvs, Docker, CI)
- Extensible: register new adapters the same way you do in TypeScript

---

## 📦 Packages

| Package | Description |
| --- | --- |
| `headless-coder-sdk-core` | Registry, shared types, cancellation helpers (`headless_coder_sdk.core`) |
| `headless-coder-sdk-codex` | Codex CLI adapter (`headless_coder_sdk.codex_sdk`) |
| `headless-coder-sdk-claude-agent` | Claude Agent SDK adapter (`headless_coder_sdk.claude_agent_sdk`, Python ≥ 3.10) |
| `headless-coder-sdk-gemini-cli` | Gemini CLI adapter (`headless_coder_sdk.gemini_cli`) |

Each package publishes under the `headless_coder_sdk.*` namespace so docs and code samples from the TS repo translate directly.

---

## 🧭 Quickstart

```bash
# optional: create a venv
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements-dev.txt  # formatter + pytest + ruff
pip install -e packages/core -e packages/codex-sdk  # install the pieces you need
```

```python
import asyncio

from headless_coder_sdk.core import create_coder, register_adapter
from headless_coder_sdk.codex_sdk import CODER_NAME as CODEX, create_adapter as create_codex

register_adapter(create_codex)

coder = create_coder(CODEX, {"workingDirectory": ".", "skipGitRepoCheck": True})
thread = await coder.start_thread()
result = await thread.run("Write a hello world script")
print(result.text)
```

---

## ▶️ Basic Run (Codex)

```python
from headless_coder_sdk.core import create_coder, register_adapter
from headless_coder_sdk.codex_sdk import CODER_NAME as CODEX_CODER, create_adapter as create_codex_adapter

register_adapter(create_codex_adapter)

coder = create_coder(
    CODEX_CODER,
    {
        "workingDirectory": "/path/to/repo",
        "sandboxMode": "workspace-write",
        "skipGitRepoCheck": True,
    },
)
thread = await coder.start_thread()
result = await thread.run("Generate a test plan for the API gateway.")
print(result.text)
```

---

## 🌊 Streaming Example (Claude)

```python
from headless_coder_sdk.core import create_coder, register_adapter
from headless_coder_sdk.claude_agent_sdk import CODER_NAME as CLAUDE_CODER, create_adapter as create_claude_adapter

register_adapter(create_claude_adapter)

claude = create_coder(
    CLAUDE_CODER,
    {
        "workingDirectory": "/repo",
        "permissionMode": "bypassPermissions",
        "allowedTools": ["Write", "Edit", "Read", "NotebookEdit"],
    },
)
thread = await claude.start_thread()
async for event in thread.run_streamed("Plan end-to-end tests"):
    if event["type"] == "message" and event.get("role") == "assistant":
        text = event.get("text", "")
        print(text, end="" if event.get("delta") else "\n")

resumed = await claude.resume_thread(thread.id)
follow_up = await resumed.run("Summarise the agreed test plan.")
print(follow_up.text)
```

---

## 🧩 Structured Output (Gemini)

```python
from headless_coder_sdk.core import create_coder, register_adapter
from headless_coder_sdk.gemini_cli import CODER_NAME as GEMINI_CODER, create_adapter as create_gemini_adapter

register_adapter(create_gemini_adapter)

gemini = create_coder(
    GEMINI_CODER,
    {
        "workingDirectory": "/repo",
        "includeDirectories": ["/repo"],
        "yolo": True,
    },
)
thread = await gemini.start_thread()
schema = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "components": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "components"],
}
turn = await thread.run("Summarise the repo in JSON", {"outputSchema": schema})
print(turn.json)
```

> ⚠️ Gemini CLI resume support is still pending upstream; the Python adapter matches the TypeScript behaviour and will skip resume tests until the CLI adds it.

---

## 🔁 Resume Example (Codex)

```python
register_adapter(create_codex_adapter)

codex = create_coder(
    CODEX_CODER,
    {
        "workingDirectory": "/repo",
        "sandboxMode": "workspace-write",
        "skipGitRepoCheck": True,
    },
)
session = await codex.start_thread({"model": "gpt-5-codex"})
await session.run("Draft a CLI plan.")

resumed = await codex.resume_thread(session.id)
follow_up = await resumed.run("Continue with implementation details.")
print(follow_up.text)
```

---

## 🔄 Multi-Provider Workflow

```python
from headless_coder_sdk.core import create_coder, register_adapter
from headless_coder_sdk.codex_sdk import CODER_NAME as CODEX, create_adapter as create_codex
from headless_coder_sdk.claude_agent_sdk import CODER_NAME as CLAUDE, create_adapter as create_claude
from headless_coder_sdk.gemini_cli import CODER_NAME as GEMINI, create_adapter as create_gemini

register_adapter(create_codex)
register_adapter(create_claude)
register_adapter(create_gemini)

async def run_multi_provider_review(commit_hash: str) -> None:
    review_schema = {
        "type": "object",
        "properties": {
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "description": {"type": "string"},
                        "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["file", "description", "severity"],
                },
            },
        },
        "required": ["issues"],
    }

    claude, codex = create_coder(CLAUDE), create_coder(CODEX)
    claude_thread, codex_thread = await asyncio.gather(claude.start_thread(), codex.start_thread())

    review_prompt = lambda name: (
        f"Review commit {commit_hash} as {name}. Focus on regressions, tests, and security."
    )

    claude_review, codex_review = await asyncio.gather(
        claude_thread.run(review_prompt("Claude"), {"outputSchema": review_schema}),
        codex_thread.run(review_prompt("Codex"), {"outputSchema": review_schema}),
    )

    combined = [*(claude_review.json or {}).get("issues", []), *(codex_review.json or {}).get("issues", [])]

    gemini = create_coder(GEMINI, {"workingDirectory": "/repo"})
    gemini_thread = await gemini.start_thread()
    for issue in combined:
        await gemini_thread.run(
            [
                {"role": "system", "content": "Fix review issues one at a time. Apply patches directly."},
                {
                    "role": "user",
                    "content": (
                        f"Commit: {commit_hash}\nFile: {issue['file']}\nSeverity: {issue['severity']}\n"
                        f"Issue: {issue['description']}\nPlease fix this issue and describe the change."
                    ),
                },
            ]
        )

    await asyncio.gather(
        claude.close(claude_thread),
        codex.close(codex_thread),
        gemini.close(gemini_thread),
    )
```

---

## ⏹️ Handling Interrupts

```python
from headless_coder_sdk.core import AbortController

coder = create_coder(CODEX_CODER, {"workingDirectory": "/repo"})
controller = AbortController()
thread = await coder.start_thread()
run_task = asyncio.create_task(thread.run("Generate a summary of CONTRIBUTING.md", {"signal": controller.signal}))
await asyncio.sleep(2)
controller.abort("User cancelled")

try:
    await run_task
except Exception as exc:
    if getattr(exc, "code", None) == "interrupted":
        print("Run aborted as expected.")
```

Streams emit a `cancelled` event and `run` raises an `AbortError` (code `interrupted`).

---

## 🧪 Tests & Examples

- Package unit tests live in `packages/*/tests`. Run them with the provided `PYTHONPATH` hints in the previous README.
- The TypeScript example suite now has Python twins under `examples/tests`. They expect real provider binaries/keys but skip cleanly when unavailable.
  ```bash
  # run everything, skipping providers that aren't configured
  PYTHONPATH=packages/core/src:packages/codex-sdk/src:packages/gemini-cli/src:packages/claude-agent-sdk/src \
    python3 -m pytest examples/tests
  ```
- Calculator validations rely on Node.js + `jsdom` (install from the TS repo’s `node_modules` or run `npm i` there).

Provider prerequisites:

| Adapter | Requirement |
| --- | --- |
| Codex | Licensed `codex` binary on PATH or `CODEX_EXECUTABLE_PATH` pointing to it |
| Gemini | `gemini` CLI installed and authenticated; resume currently unsupported |
| Claude | Python ≥ 3.10 and `claude-agent-sdk>=0.1.6`, plus `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, `ANTHROPIC_API_TOKEN`, or `AWS_BEARER_TOKEN_BEDROCK` |

---

## ⚙️ Development

```bash
pip install -r requirements-dev.txt
ruff check
PYTHONPATH=packages/core/src python3 -m pytest packages/core/tests
PYTHONPATH=packages/core/src:packages/codex-sdk/src:packages/gemini-cli/src:packages/claude-agent-sdk/src \
  python3 -m pytest examples/tests -q
```

We mirror the TypeScript repo’s contribution guidelines: keep modules small, document public APIs with Google-style docstrings, and add tests for new capabilities.

---

## 🧱 Build Your Own Adapter

Need another provider? Import `register_adapter` from `headless_coder_sdk.core`, implement the `HeadlessCoder` protocol, and expose a `create_adapter` function with a `coder_name` attribute. The TypeScript [Create Your Own Adapter](https://github.com/OhadAssulin/headless-coder-sdk/blob/main/docs/create-your-own-adapter.md) guide applies directly—swap TypeScript types for Python protocols.

---

## 💬 Feedback & Contributing

Issues and PRs are welcome. Please open them on this repository once it moves out of the mirror stage, or use the TypeScript repo for cross-language discussions.

---

© 2025 Ohad Assulin - MIT License
