# ADR-0010 · One API behind every client; agents are first-class users

**Status:** accepted · 2026-10-02 (decisions L1, L2, L3, H5, J4)

## Context

The brief was extended on 2026-10-02: Stokes must be LLM-compatible from the
beginning, with tool calls and API or server back ends so agents can drive
it. Separately, the product must support scripting and journaling, and the
embedded assistant and MCP server are paid features while the plain API, CLI
and headless server are free.

## Decision

1. **One Python API** (`stokes.api`) is the only way to change or query the
   model. The Qt UI, the `stokes` CLI, the HTTP/JSON-RPC server, the MCP
   server, the embedded assistant and journal replay are all clients of it.
2. **Commands are typed and registered.** Each command has a JSON schema
   derived from the node schemas. The MCP tool list, the OpenAPI document and
   the CLI help are generated from the same registry, so they cannot drift.
3. **Surfaces**:
   - `stokes serve` runs the HTTP/JSON-RPC server with OpenAPI and event
     streaming, with or without a window (headless mode). Free.
   - `stokes mcp` (and the in-app MCP endpoint) exposes commands as MCP tools
     and project state as MCP resources. Paid flag `assistant`.
   - The `stokes` CLI offers every command with JSON output and replays
     journals. Free.
   - The embedded assistant panel runs a tool-use loop over the API with a
     provider abstraction: Anthropic (Claude), OpenAI, Gemini, and any
     OpenAI-compatible local server (LM Studio, Ollama). Keys live in the OS
     keychain; local servers need none. Paid flag `assistant`.
4. **Agents have full control.** No confirmation prompts for launching,
   cancelling or deleting. An optional "confirm destructive actions" setting
   exists and is off by default. Everything is journaled and undoable.
5. **Events**: progress, state changes and monitor values stream to every
   client the same way (Qt signals in-process, SSE or WebSocket over HTTP,
   MCP notifications where supported).
6. **Licence gating happens in the API**, so UI, CLI, HTTP and MCP are gated
   identically.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Add an API after the UI exists | Retrofitting an API-first design is the one thing that cannot be done later; every panel would carry logic the API lacks. |
| MCP only, no HTTP | Non-MCP agent frameworks and plain scripts need HTTP or Python; both are cheap once the registry exists. |
| Confirmation prompts for agents by default | Contradicts decision L3 and makes campaigns impossible; journals and undo are the safety net instead. |
| A single LLM provider | The user runs a local LM Studio and wants provider choice; the tool-use loop is provider-agnostic by design. |

## Consequences

- Every feature starts as an API command with a schema and a test; the UI
  comes second. This slows the first panel and speeds up every later one.
- Tutorials, regression tests and agent demonstrations are the same
  artifact: a journal.
- The server must marshal commands onto the main thread safely; this is a
  framework-level concern solved in M0.
- Provider SDKs (anthropic MIT, openai Apache-2.0, google-genai Apache-2.0,
  mcp MIT) join the dependency audit; all are permissive.
