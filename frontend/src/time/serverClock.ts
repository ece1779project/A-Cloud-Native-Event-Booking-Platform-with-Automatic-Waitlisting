let offsetMs = 0;

export function calibrateServerClock(
  serverTime: string,
  receivedAtMs = Date.now(),
): void {
  const parsed = Date.parse(serverTime);
  if (!Number.isNaN(parsed)) offsetMs = parsed - receivedAtMs;
}

export function serverNow(localNowMs = Date.now()): number {
  return localNowMs + offsetMs;
}
