# ADR-0009 · Distribution, protection and licensing mechanics

**Status:** accepted · 2026-10-02 (decisions A2, I3, J1, J2, J3, J4, B2)

## Context

Stokes is a closed-source freemium desktop product with no engine software
shipped. The free tier is the full local workflow with no limits on cells,
cores or jobs; paid features are remote execution, the Fluent engine, the
embedded assistant with the MCP server, and reports with run comparison and
sweeps.

## Decision

- **Build**: Nuitka standalone builds (Apache-2.0) of the desktop and the
  agent; shared libraries (Qt, OCCT, VTK) stay separate files for LGPL
  replaceability. PyInstaller (GPL with bootloader exception) is the fallback
  bundler.
- **Installers**: Windows installer (Inno Setup or WiX) signed with an
  Authenticode certificate; Linux tarball and AppImage signed with GPG. No
  WSL image, no engines.
- **Licence files**: JSON payload with an Ed25519 signature; the public key
  compiled into the app. Flags: `remote`, `fluent`, `assistant`, `reports`.
  Node-locked by a salted hardware fingerprint tolerant to one component
  change, or "any machine" for personal licences. Offline activation by file
  exchange; a floating server only if ever needed.
- **Free tier**: runs with no licence file at all.
- **Enforcement**: in the API, so every client is gated identically; soft at
  start, hard at the gated command. GPL engines are never restricted.
- **Updates**: an updater that checks a signed manifest when the user allows
  it.
- **Telemetry**: none by default; opt-in local crash bundles.
- **Protection**: compilation and signing only; no obfuscation; no private
  secrets in the product.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Licensing SaaS (Cryptlex, Keygen, LicenseSpring) | An online dependency and a vendor; reconsider only if billing becomes operational pain. |
| Obfuscation (PyArmor) | Marginal over compilation; adds a commercial tool and build fragility. |
| Compute caps in the free tier | Rejected by decision I3; the free tier competes on being unlimited locally. |
| Static single-binary packaging | Breaks LGPL replaceability. |

## Consequences

- Build infrastructure is a milestone of its own (M8).
- Legal review of licence terms and notices before the public beta.
