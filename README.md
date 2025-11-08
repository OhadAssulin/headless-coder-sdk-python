# python-headless-coder

Python port of the `headless-coder-sdk`, exposing a unified interface for headless AI coders (Codex, Claude, Gemini). The repository mirrors the TypeScript workspace layout so that documentation and mental models can be shared between ecosystems.

## Packages

| Path | Description |
| --- | --- |
| `packages/core` | Registry, shared types, cancellation primitives |
| `packages/gemini-cli` | Gemini CLI adapter |
| `packages/claude-agent-sdk` | Claude Agent SDK adapter *(requires Python ≥ 3.10 when installing the upstream dependency)* |
| `packages/codex-sdk` | Codex CLI adapter |

Every package publishes under the `headless_coder_sdk.*` namespace (e.g., `headless_coder_sdk.core`, `headless_coder_sdk.codex_sdk`), matching the TypeScript package names.

## Development

```bash
# (optional) create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# install formatter/test tooling
pip install -r requirements-dev.txt
```

Run the unit tests for each package with the corresponding `PYTHONPATH`:

```bash
PYTHONPATH=packages/core/src python3 -m pytest packages/core/tests
PYTHONPATH=packages/core/src:packages/gemini-cli/src python3 -m pytest packages/gemini-cli/tests
PYTHONPATH=packages/core/src:packages/claude-agent-sdk/src python3 -m pytest packages/claude-agent-sdk/tests
PYTHONPATH=packages/core/src:packages/codex-sdk/src python3 -m pytest packages/codex-sdk/tests
```

When integrating with real providers, install the relevant package(s) from `packages/*` and follow the upstream provider documentation for API keys, CLI binaries, and sandbox setup.
