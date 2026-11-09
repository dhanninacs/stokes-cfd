#!/usr/bin/env python3
"""Drive the free model and Hive over HTTP, so a task can be routed by hand.

## Why this exists instead of the `workflow` tool

`workflow` is the only delegation in DSH that can pin a provider and model, and **it cannot run on
this
host**: `dsh-workflow-ptc` requires the Node TypeScript PTC runtime, and Node v22.22.1 here refuses
to run
TypeScript at all (`ERR_NO_TYPESCRIPT`, "Node.js is not compiled with TypeScript support"). So the
tiering the owner asked for — the free model for the work, Hive to review — is driven directly.

This is a **tool, not a gate**: it sends a prompt, prints the answer, and does nothing else. Every
answer it returns is a suggestion the caller must check against the tests. Nothing here writes to
the
repository.

Usage:
    python tools/ask_model.py free  "prompt"            # cohere/north-mini-code:free
    python tools/ask_model.py hive  "prompt"            # deepseek-ai/deepseek-v4.1-flash
    python tools/ask_model.py free  --file a.py --file b.py "review these"
    python tools/ask_model.py free  --json              # the raw reply object, for scripting

The prompt comes from argv or stdin. `--file` splices files in with a header, which is how a bounded
task gets its context: a pinned agent has none, and neither has this.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

# The two routes, by the names the owner uses. Neither id is invented: both are served by their
# catalogue, and a wrong id here fails as an HTTP error rather than silently passing.
ROUTES: dict[str, tuple[str, str, str]] = {
    # name: (base URL, model id, the env name holding the key)
    "free": (
        "https://openrouter.ai/api/v1/chat/completions",
        "cohere/north-mini-code:free",
        "OPENROUTER_API_KEY",
    ),
    "hive": (
        "https://api-cdn.thehive.ai/api/v3/chat/completions",
        "deepseek-ai/deepseek-v4.1-flash",
        "HIVE_AI_API_KEY",
    ),
}

# The free model reasons before it answers, and a small budget is spent thinking: a 20-token call
# comes
# back with `content: null`, which reads exactly like a broken route and is not one. Hundreds, not
# tens.
DEFAULT_TOKENS = 4000


def api_key(name: str) -> str:
    """The key for a route, from the environment or the harness credentials file.

    The file is the harness's own store; reading it here keeps one copy of the secret rather than a
    second one in this repository, which would be an IP and a security problem both.
    """
    env = os.environ.get(name)
    if env:
        return env
    creds = pathlib.Path.home() / ".dsh" / ".credentials.yaml"
    try:
        import yaml

        refs = (yaml.safe_load(creds.read_text()) or {}).get("refs") or {}
    except (OSError, ImportError) as exc:
        raise SystemExit(f"no {name} in the environment and {creds} is unreadable: {exc}") from exc
    key = refs.get(name)
    if not isinstance(key, str) or not key:
        raise SystemExit(f"no {name} in the environment or in {creds}")
    return key


def ask(route: str, prompt: str, tokens: int, system: str | None) -> str:
    """Send one prompt and return the reply's text. Raises `SystemExit` on any failure."""
    url, model, env = ROUTES[route]
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = json.dumps({"model": model, "messages": messages, "max_tokens": tokens}).encode()
    # `S310` again, on the `Request` that carries the same literal URL (see below).
    request = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        headers={"Authorization": "Bearer " + api_key(env), "Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        # `S310` warns about `urlopen` taking a scheme from data. The URL is not from data: it is
        # one of two literals in `ROUTES`, picked by an `argparse` choice, so it cannot be `file:`.
        with urllib.request.urlopen(  # noqa: S310
            request, timeout=180
        ) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise SystemExit(f"{route} ({model}) failed: HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"{route} ({model}) failed: {exc}") from exc
    elapsed = time.monotonic() - started
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content")
    if not text:
        # Say WHY the content is empty rather than printing an empty answer: a reasoning model that
        # spent its budget reports `finish_reason: length`, and that is a different problem from a
        # refusal, which reports `refusal`.
        reason = choice.get("finish_reason")
        refusal = message.get("refusal")
        why = f"finish_reason={reason}" + (f", refusal={refusal!r}" if refusal else "")
        raise SystemExit(
            f"{route} ({model}) returned no content after {elapsed:.1f}s ({why}). "
            f"A reasoning model needs a real --tokens budget; try --tokens {tokens * 2}."
        )
    finish = choice.get("finish_reason")
    print(f"# {route}: {model}, {elapsed:.1f}s, finish={finish}", file=sys.stderr)
    return str(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ask-model", description=__doc__)
    parser.add_argument("route", choices=sorted(ROUTES), help="which route to ask")
    parser.add_argument("prompt", nargs="?", help="the prompt; omit to read it from stdin")
    parser.add_argument(
        "--file", action="append", default=[], help="a file to splice in (repeatable)"
    )
    parser.add_argument("--system", default=None, help="a system message")
    parser.add_argument("--tokens", type=int, default=DEFAULT_TOKENS, help="max output tokens")
    parser.add_argument("--json", action="store_true", help="print nothing but the reply text")
    args = parser.parse_args(argv)

    text = args.prompt if args.prompt is not None else sys.stdin.read()
    for name in args.file:
        path = pathlib.Path(name)
        text += f"\n\n===== {name} =====\n{path.read_text()}"

    answer = ask(args.route, text, args.tokens, args.system)
    if not args.json:
        print(answer)
    else:
        print(json.dumps({"route": args.route, "text": answer}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
