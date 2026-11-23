import type {components} from "../api/schema";

type Participation = components["schemas"]["ParticipationView"];

/** One card per session: the active attempt, otherwise the newest terminal one. */
export function currentRegistrations(items: Participation[]): Participation[] {
  const bySession = new Map<string, Participation>();
  for (const item of items) {
    const current = bySession.get(item.session_id);
    if (current === undefined || isCurrentAttempt(item, current)) {
      bySession.set(item.session_id, item);
    }
  }
  return [...bySession.values()].sort(byConfirmationUrgency);
}

function isCurrentAttempt(
  candidate: Participation,
  current: Participation,
): boolean {
  if (candidate.historical !== current.historical) return !candidate.historical;
  if (candidate.created_at !== current.created_at) {
    return candidate.created_at > current.created_at;
  }
  return candidate.id > current.id;
}

function byConfirmationUrgency(
  left: Participation,
  right: Participation,
): number {
  const leftDue = isTimeLimited(left);
  const rightDue = isTimeLimited(right);
  if (leftDue !== rightDue) return leftDue ? -1 : 1;
  if (leftDue && rightDue) {
    return (left.deadline_at ?? "").localeCompare(right.deadline_at ?? "");
  }
  if (left.created_at !== right.created_at) {
    return left.created_at < right.created_at ? 1 : -1;
  }
  return left.id < right.id ? 1 : -1;
}

function isTimeLimited(item: Participation): boolean {
  return item.state === "HELD" || item.state === "OFFERED";
}
