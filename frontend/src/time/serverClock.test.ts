import {describe, expect, it} from "vitest";

import {calibrateServerClock, serverNow} from "./serverClock";

describe("serverClock", () => {
  it("uses response receipt time to estimate server time", () => {
    calibrateServerClock("2026-11-18T12:00:00Z", 1_000);
    expect(serverNow(2_500)).toBe(Date.parse("2026-11-18T12:00:00Z") + 1_500);
  });

  it("ignores malformed server timestamps", () => {
    const before = serverNow(10_000);
    calibrateServerClock("not-a-time", 10_000);
    expect(serverNow(10_000)).toBe(before);
  });
});
