import {describe, expect, it} from "vitest";

import {VersionTracker} from "./versionTracker";

describe("VersionTracker", () => {
  it("detects stale, contiguous, duplicate, and gap observations", () => {
    const tracker = new VersionTracker();
    expect(tracker.observe("session-a", 4)).toBe("current");
    expect(tracker.observe("session-a", 5)).toBe("current");
    expect(tracker.observe("session-a", 5)).toBe("current");
    expect(tracker.observe("session-a", 3)).toBe("stale");
    expect(tracker.observe("session-a", 8)).toBe("gap");
  });

  it("tracks sessions independently", () => {
    const tracker = new VersionTracker();
    tracker.observe("session-a", 10);
    expect(tracker.observe("session-b", 2)).toBe("current");
  });
});
