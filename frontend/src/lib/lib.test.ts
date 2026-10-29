import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, buildQuery, request } from "./api";
import { backoffDelay, LiveConnection, type LiveStatus } from "./live";

describe("buildQuery", () => {
  it("drops empty values and encodes the rest", () => {
    expect(buildQuery({ q: "a b", status: "", page: 2, x: undefined, y: null, inactive: false })).toBe("?q=a+b&page=2&inactive=false");
    expect(buildQuery({})).toBe("");
  });
});

describe("request", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("parses the backend error envelope into ApiError with field errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "validation_error",
              message: "Request validation failed",
              details: [{ loc: ["body", "code"], msg: "String should match pattern" }],
              request_id: "abc123",
            },
          }),
          { status: 422 },
        ),
      ),
    );
    const err = (await request("POST", "/api/equipment", { body: {} }).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.requestId).toBe("abc123");
    expect(err.fieldErrors()).toEqual({ code: "String should match pattern" });
  });

  it("maps network failures to status 0 with a helpful message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const err = (await request("GET", "/api/equipment").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toMatch(/Cannot reach/);
  });
});

describe("backoffDelay", () => {
  it("grows exponentially and is bounded", () => {
    const noJitter = { initialMs: 1000, maxMs: 30000, factor: 2, jitter: 0 };
    expect([0, 1, 2, 3, 4, 5, 10].map((a) => backoffDelay(a, noJitter))).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  });
  it("applies jitter within ±20% and never exceeds the cap", () => {
    const o = { initialMs: 1000, maxMs: 30000, factor: 2, jitter: 0.2 };
    expect(backoffDelay(0, o, () => 0)).toBe(800);
    expect(backoffDelay(0, o, () => 1)).toBe(1200);
    expect(backoffDelay(20, o, () => 1)).toBe(30000);
  });
});

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  close() {
    this.closed = true;
  }
}

describe("LiveConnection", () => {
  afterEach(() => {
    vi.useRealTimers();
    FakeSocket.instances = [];
  });

  it("reconnects with backoff, refreshes on reconnect, and parses messages", () => {
    vi.useFakeTimers();
    const statuses: LiveStatus[] = [];
    const onReconnect = vi.fn();
    const onMessage = vi.fn();
    const conn = new LiveConnection(
      "ws://x/ws/live",
      { onStatus: (s) => statuses.push(s), onReconnect, onMessage },
      (u) => new FakeSocket(u) as unknown as WebSocket,
      { initialMs: 100, maxMs: 1000, factor: 2, jitter: 0 },
    );
    conn.start();
    const first = FakeSocket.instances[0]!;
    first.onopen?.();
    expect(onReconnect).not.toHaveBeenCalled(); // first connect is not a reconnect
    first.onmessage?.({ data: JSON.stringify({ type: "alert", ts: "t", data: { id: 1 } }) });
    first.onmessage?.({ data: "not json" });
    expect(onMessage).toHaveBeenCalledTimes(1);

    first.onclose?.(); // server went away
    expect(statuses.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(99);
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    const second = FakeSocket.instances[1]!;
    second.onclose?.(); // still down: next delay doubles
    vi.advanceTimersByTime(199);
    expect(FakeSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    const third = FakeSocket.instances[2]!;
    third.onopen?.();
    expect(statuses.at(-1)).toBe("open");
    expect(onReconnect).toHaveBeenCalledTimes(1);

    conn.stop();
    expect(third.closed).toBe(true);
    expect(statuses.at(-1)).toBe("closed");
    third.onclose?.();
    vi.advanceTimersByTime(5000);
    expect(FakeSocket.instances).toHaveLength(3); // no reconnect after stop
  });
});
