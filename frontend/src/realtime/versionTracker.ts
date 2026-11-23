export type VersionObservation = "current" | "gap" | "stale";

export class VersionTracker {
  private versions = new Map<string, number>();

  observe(sessionId: string, version: number): VersionObservation {
    const previous = this.versions.get(sessionId);
    if (previous !== undefined && version < previous) return "stale";
    this.versions.set(sessionId, version);
    return previous !== undefined && version > previous + 1 ? "gap" : "current";
  }
}
