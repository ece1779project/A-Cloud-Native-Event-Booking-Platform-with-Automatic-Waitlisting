import {useEffect, useRef, useState} from "react";

import {serverNow} from "./serverClock";

export function DeadlineCountdown({
  deadline,
  onExpired,
}: {
  deadline: string;
  onExpired: () => void;
}) {
  const target = Date.parse(deadline);
  const [remaining, setRemaining] = useState(() => target - serverNow());
  const expired = useRef(false);

  useEffect(() => {
    const update = () => {
      const next = target - serverNow();
      setRemaining(next);
      if (next <= 0 && !expired.current) {
        expired.current = true;
        onExpired();
      }
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [onExpired, target]);

  if (remaining <= 0) return <span>Deadline reached; refreshing…</span>;
  const seconds = Math.ceil(remaining / 1000);
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return <span>Approximately {minutes}:{rest.toString().padStart(2, "0")} remaining</span>;
}
