import type {QueryClient} from "@tanstack/react-query";

import {api} from "../api/client";
import {VersionTracker} from "./versionTracker";

export type RealtimeStatus = "connecting" | "connected" | "disconnected";

type RealtimeMessage = {
  type: string;
  session_id?: string;
  participation_id?: string;
  event_id?: string;
  version?: number;
  sessions?: Array<{session_id: string; version: number}>;
};

const RECONCILE_INTERVAL_MS = 60_000;
const MAX_RECONNECT_MS = 30_000;

export class SeatflowRealtime {
  private socket: WebSocket | null = null;
  private desiredChannels = new Set<string>();
  private activeChannels = new Set<string>();
  private grantedScopes = new Set<string>();
  private versions = new VersionTracker();
  private reconnectAttempt = 0;
  private reconnectTimer: number | null = null;
  private reconcileTimer: number | null = null;
  private stopped = true;

  constructor(
    private readonly queryClient: QueryClient,
    private readonly onStatus: (status: RealtimeStatus) => void,
    private readonly onAuthRevoked: () => void,
  ) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    this.socket?.close(1000, "client shutdown");
    this.socket = null;
    this.activeChannels.clear();
    this.onStatus("disconnected");
  }

  setChannels(channels: Iterable<string>): void {
    this.desiredChannels = new Set(channels);
    this.syncSubscriptions();
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;
    this.onStatus("connecting");

    let ticket: string;
    let scopes: readonly string[];
    try {
      const {data, error} = await api.POST("/realtime/tickets");
      if (error !== undefined || data === undefined) {
        throw new Error("ticket issuance failed");
      }
      ticket = data.data.ticket;
      scopes = data.data.scopes;
    } catch {
      this.onStatus("disconnected");
      this.scheduleReconnect();
      return;
    }

    this.grantedScopes = new Set(scopes);
    const socket = new WebSocket(this.websocketUrl(), [
      "seatflow.v1",
      `seatflow.ticket.${ticket}`,
    ]);
    this.socket = socket;

    socket.addEventListener("message", (event) => this.handleMessage(event.data));
    socket.addEventListener("close", (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.activeChannels.clear();
      this.stopReconciliation();
      this.onStatus("disconnected");
      if (event.code === 4401 || event.code === 4403) {
        this.onAuthRevoked();
        return;
      }
      this.scheduleReconnect();
    });
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== "string") return;
    let message: RealtimeMessage;
    try {
      message = JSON.parse(raw) as RealtimeMessage;
    } catch {
      return;
    }

    if (message.type === "connection.ready") {
      this.reconnectAttempt = 0;
      this.onStatus("connected");
      this.activeChannels.clear();
      this.syncSubscriptions();
      this.startReconciliation();
      void this.queryClient.invalidateQueries();
      return;
    }
    if (message.type === "auth.revoked") {
      this.onAuthRevoked();
      return;
    }
    if (message.type === "system.resync_required") {
      void this.queryClient.invalidateQueries();
      return;
    }
    if (message.type === "session.changed" && this.acceptVersion(message)) {
      void this.queryClient.invalidateQueries({
        queryKey: ["session", message.session_id],
      });
      void this.queryClient.invalidateQueries({queryKey: ["events"]});
      void this.queryClient.invalidateQueries({
        queryKey: ["my-participation", message.session_id],
      });
      return;
    }
    if (message.type === "participation.changed" && this.acceptVersion(message)) {
      void this.queryClient.invalidateQueries({queryKey: ["my-participations"]});
      void this.queryClient.invalidateQueries({
        queryKey: ["participation", message.participation_id],
      });
      return;
    }
    if (message.type === "management.changed") {
      for (const session of message.sessions ?? []) {
        this.acceptVersion({...message, ...session});
      }
      void this.queryClient.invalidateQueries({
        queryKey: ["management", message.event_id],
      });
      void this.queryClient.invalidateQueries({queryKey: ["my-events"]});
    }
  }

  private acceptVersion(message: RealtimeMessage): boolean {
    if (message.session_id === undefined || message.version === undefined) {
      return true;
    }
    const observation = this.versions.observe(
      message.session_id,
      message.version,
    );
    if (observation === "stale") return false;
    if (observation === "gap") {
      void this.queryClient.invalidateQueries();
    }
    return true;
  }

  private syncSubscriptions(): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    const allowed = new Set([...this.desiredChannels].filter((channel) => {
      if (channel === "me:participations") {
        return this.grantedScopes.has("account");
      }
      if (channel.endsWith(":management")) {
        return this.grantedScopes.has("management");
      }
      return this.grantedScopes.has("public");
    }));
    const removed = [...this.activeChannels].filter(
      (channel) => !allowed.has(channel),
    );
    const added = [...allowed].filter(
      (channel) => !this.activeChannels.has(channel),
    );
    if (removed.length > 0) this.sendSubscriptionCommand("unsubscribe", removed);
    if (added.length > 0) this.sendSubscriptionCommand("subscribe", added);
    this.activeChannels = allowed;
  }

  private sendSubscriptionCommand(
    type: "subscribe" | "unsubscribe",
    channels: string[],
  ): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    this.socket.send(
      JSON.stringify({
        type,
        request_id: crypto.randomUUID(),
        channels,
      }),
    );
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer !== null) return;
    const base = Math.min(1000 * 2 ** this.reconnectAttempt, MAX_RECONNECT_MS);
    const delay = base * (0.75 + Math.random() * 0.5);
    this.reconnectAttempt += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      void this.connect();
    }, delay);
  }

  private startReconciliation(): void {
    this.stopReconciliation();
    this.reconcileTimer = window.setInterval(() => {
      void this.queryClient.invalidateQueries();
    }, RECONCILE_INTERVAL_MS);
  }

  private stopReconciliation(): void {
    if (this.reconcileTimer !== null) window.clearInterval(this.reconcileTimer);
    this.reconcileTimer = null;
  }

  private clearTimers(): void {
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.stopReconciliation();
  }

  private websocketUrl(): string {
    if (import.meta.env.VITE_WS_URL !== undefined) {
      return import.meta.env.VITE_WS_URL;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}/ws/v1`;
  }
}
