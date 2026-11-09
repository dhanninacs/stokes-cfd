import { afterEach, describe, expect, it, vi } from "vitest";
import { StokesClient } from "./client";

/** A WebSocket that opens at once and lets the test answer what the client sent. */
class FakeSocket {
  static OPEN = 1;
  static last: FakeSocket | null = null;
  readyState = 1;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor() {
    FakeSocket.last = this;
    queueMicrotask(() => this.onopen?.());
  }
  send(text: string): void {
    this.sent.push(text);
  }
  close(): void {}
  reply(text: string): void {
    this.onmessage?.({ data: text });
  }
}

afterEach(() => vi.unstubAllGlobals());

async function connected(): Promise<{ client: StokesClient; socket: FakeSocket }> {
  vi.stubGlobal("WebSocket", FakeSocket);
  const client = new StokesClient("http://127.0.0.1:1", "t");
  await client.connect();
  return { client, socket: FakeSocket.last as FakeSocket };
}

describe("StokesClient replies", () => {
  it("settles a request whose reply is not valid JSON instead of leaving it waiting", async () => {
    const { client, socket } = await connected();
    const call = client.call("run.list");
    const { id } = JSON.parse(socket.sent[0] ?? "{}") as { id: number };
    // What the server once sent for a diverged run's monitors: `NaN` is not JSON.
    socket.reply(`{"id": ${id}, "result": {"runs": [{"flow_rate": NaN}]}}`);
    await expect(call).rejects.toThrow("could not be read");
  });

  it("resolves an ordinary reply", async () => {
    const { client, socket } = await connected();
    const call = client.call("run.list");
    const { id } = JSON.parse(socket.sent[0] ?? "{}") as { id: number };
    socket.reply(JSON.stringify({ id, result: { runs: [] } }));
    await expect(call).resolves.toEqual({ runs: [] });
  });
});
