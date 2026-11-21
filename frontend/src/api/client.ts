import createClient from "openapi-fetch";

import type {paths} from "./schema";
import {calibrateServerClock} from "../time/serverClock";

let accessToken: string | null = null;
const tokenListeners = new Set<() => void>();

export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? "/api/v1",
  credentials: "include",
});

api.use({
  async onRequest({request}) {
    if (accessToken !== null) {
      request.headers.set("Authorization", `Bearer ${accessToken}`);
    }
    return request;
  },
  async onResponse({response}) {
    if (response.headers.get("content-type")?.includes("application/json")) {
      const body = await response.clone().json() as {
        meta?: {server_time?: unknown};
      };
      if (typeof body.meta?.server_time === "string") {
        calibrateServerClock(body.meta.server_time);
      }
    }
    return response;
  },
});

export function setAccessToken(token: string | null): void {
  accessToken = token;
  for (const listener of tokenListeners) listener();
}

export function onAccessTokenChange(listener: () => void): () => void {
  tokenListeners.add(listener);
  return () => tokenListeners.delete(listener);
}
