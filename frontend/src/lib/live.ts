import type { LiveMessage } from "./types";

export type LiveStatus = "connecting" | "open" | "reconnecting" | "closed";

export interface BackoffOptions {
  initialMs: number;
  maxMs: number;
  factor: number;
  jitter: number; // 0..1 fraction of the delay
}

export const DEFAULT_BACKOFF: BackoffOptions = { initialMs: 1000, maxMs: 30000, factor: 2, jitter: 0.2 };

/** Bounded exponential backoff: initial * factor^attempt, capped at maxMs, ± jitter. */
export function backoffDelay(attempt: number, o: BackoffOptions = DEFAULT_BACKOFF, random = Math.random): number {
  const base = Math.min(o.maxMs, o.initialMs * o.factor ** attempt);
  const spread = base * o.jitter;
  return Math.round(Math.min(o.maxMs, Math.max(0, base - spread + random() * 2 * spread)));
}

export interface LiveCallbacks {
  onMessage: (msg: LiveMessage) => void;
  onStatus: (status: LiveStatus, info: { attempt: number; nextRetryMs: number | null }) => void;
  /** Called after a successful *re*connect so stale server state can be refetched. */
  onReconnect: () => void;
}

type SocketFactory = (url: string) => WebSocket;

/**
 * WebSocket wrapper with automatic reconnection using bounded exponential backoff.
 * Framework-agnostic so it can be unit-tested with a fake socket and fake timers.
 */
export class LiveConnection {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private hasConnected = false;
  private readonly url: string;
  private readonly cb: LiveCallbacks;
  private readonly factory: SocketFactory;
  private readonly backoff: BackoffOptions;

  constructor(url: string, cb: LiveCallbacks, factory: SocketFactory = (u) => new WebSocket(u), backoff = DEFAULT_BACKOFF) {
    this.url = url;
    this.cb = cb;
    this.factory = factory;
    this.backoff = backoff;
  }

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const ws = this.ws;
    this.ws = null;
    ws?.close();
    this.cb.onStatus("closed", { attempt: this.attempt, nextRetryMs: null });
  }

  private connect() {
    this.cb.onStatus(this.hasConnected || this.attempt > 0 ? "reconnecting" : "connecting", { attempt: this.attempt, nextRetryMs: null });
    let ws: WebSocket;
    try {
      ws = this.factory(this.url);
    } catch {
      this.scheduleRetry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      const wasReconnect = this.hasConnected || this.attempt > 0;
      this.attempt = 0;
      this.hasConnected = true;
      this.cb.onStatus("open", { attempt: 0, nextRetryMs: null });
      if (wasReconnect) this.cb.onReconnect();
    };
    ws.onmessage = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(String(ev.data)) as LiveMessage;
        if (msg && typeof msg.type === "string") this.cb.onMessage(msg);
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      if (this.ws !== ws) return; // superseded or stopped
      this.ws = null;
      this.scheduleRetry();
    };
    ws.onerror = () => {
      /* onclose follows and handles retry */
    };
  }

  private scheduleRetry() {
    if (this.stopped) return;
    const delay = backoffDelay(this.attempt, this.backoff);
    this.attempt += 1;
    this.cb.onStatus("reconnecting", { attempt: this.attempt, nextRetryMs: delay });
    this.timer = setTimeout(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }
}

export function liveUrl(): string {
  const explicit = import.meta.env.VITE_WS_URL as string | undefined;
  if (explicit) return explicit;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/live`;
}
