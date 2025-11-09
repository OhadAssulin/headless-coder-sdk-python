# headless-coder-sdk-claude-agent (Python)

Python adapter built on top of the official `claude-agent-sdk` package to expose the shared headless coder API.

## Why "claude-agent" instead of "claude-adapter"?

The naming mirrors the underlying Anthropic dependency we wrap. The TypeScript repo publishes an `@headless-coder-sdk/claude-agent-sdk` package, and this Python port keeps the same identifier so docs, scripts, and cross-references stay aligned between ecosystems. Using `claude-agent` also signals that this adapter directly shells out to Anthropic's Claude Agent SDK binary, which already owns the "agent" branding. Renaming it to `headless-coder-sdk-claude-adapter` would break that parity and make version management harder across the mono-repo, so we intentionally keep the provider's agent terminology here.
