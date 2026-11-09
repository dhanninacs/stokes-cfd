# Security policy

**Stub.**

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately to
`<SECURITY CONTACT PLACEHOLDER>` with a description, the affected version or commit, and steps to
reproduce. You will get an acknowledgement; the owner has not yet set a response time.

## Scope

Stokes treats every input as untrusted: CAD files, OpenFOAM dictionaries and logs, agent
messages, licence files, journals and LLM tool calls. Its local servers bind to `127.0.0.1` and
require a per-session token. The design rules are in `docs/engineering/security.md`.

## Supported versions

Stokes is pre-release. Only the latest commit on the default branch is supported.
