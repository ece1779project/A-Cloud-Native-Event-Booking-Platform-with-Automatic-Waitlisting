import {describe, expect, it} from "vitest";

import type {components} from "../api/schema";
import {currentRegistrations} from "./registrations";

type Participation = components["schemas"]["ParticipationView"];

function registration(
  overrides: Partial<Participation> & Pick<Participation, "id" | "session_id" | "state">,
): Participation {
  return {
    account_id: "account",
    queue_position: null,
    created_at: "2026-11-23T12:00:00Z",
    allocated_at: null,
    deadline_at: null,
    confirmed_at: null,
    ended_at: null,
    end_reason: null,
    historical: true,
    version: 1,
    session: {
      id: overrides.session_id,
      event_id: "event",
      event_title: "Event",
      name: "Session",
      time_zone: "UTC",
      starts_at: "2026-11-24T12:00:00Z",
      ends_at: "2026-11-24T13:00:00Z",
      registration_closes_at: "2026-11-24T11:00:00Z",
      status: "PUBLISHED",
      event_status: "PUBLISHED",
    },
    ...overrides,
  };
}

describe("currentRegistrations", () => {
  it("keeps the active attempt and drops older rows for the same session", () => {
    const shown = currentRegistrations([
      registration({
        id: "new",
        session_id: "session-a",
        state: "HELD",
        historical: false,
        created_at: "2026-11-23T15:00:00Z",
        deadline_at: "2026-11-23T15:05:00Z",
      }),
      registration({
        id: "old",
        session_id: "session-a",
        state: "CANCELLED",
        created_at: "2026-11-23T14:00:00Z",
      }),
      registration({
        id: "other",
        session_id: "session-b",
        state: "CONFIRMED",
        historical: false,
        created_at: "2026-11-23T13:00:00Z",
      }),
    ]);

    expect(shown.map((item) => item.id)).toEqual(["new", "other"]);
  });

  it("keeps the newest terminal attempt when the session has no active row", () => {
    const shown = currentRegistrations([
      registration({
        id: "older",
        session_id: "session-a",
        state: "EXPIRED",
        created_at: "2026-11-23T10:00:00Z",
      }),
      registration({
        id: "newer",
        session_id: "session-a",
        state: "CANCELLED",
        created_at: "2026-11-23T16:00:00Z",
      }),
    ]);

    expect(shown.map((item) => item.id)).toEqual(["newer"]);
  });

  it("lists holds and offers ahead of settled registrations", () => {
    const shown = currentRegistrations([
      registration({
        id: "confirmed",
        session_id: "session-a",
        state: "CONFIRMED",
        historical: false,
        created_at: "2026-11-23T16:00:00Z",
      }),
      registration({
        id: "held-later",
        session_id: "session-b",
        state: "HELD",
        historical: false,
        created_at: "2026-11-23T12:00:00Z",
        deadline_at: "2026-11-23T18:00:00Z",
      }),
      registration({
        id: "offered-sooner",
        session_id: "session-c",
        state: "OFFERED",
        historical: false,
        created_at: "2026-11-23T11:00:00Z",
        deadline_at: "2026-11-23T17:00:00Z",
      }),
    ]);

    expect(shown.map((item) => item.id)).toEqual([
      "offered-sooner",
      "held-later",
      "confirmed",
    ]);
  });
});
