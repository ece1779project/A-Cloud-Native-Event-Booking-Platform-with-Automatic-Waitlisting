import {useMutation, useQueryClient} from "@tanstack/react-query";
import {useRef} from "react";

import {api} from "../api/client";

export type ParticipationAction =
  | "book"
  | "waitlist"
  | "confirm"
  | "cancel"
  | "decline"
  | "leave";

type Props = {
  action: ParticipationAction;
  targetId: string;
  children: string;
  confirmMessage?: string;
};

export function CommandButton({
  action,
  targetId,
  children,
  confirmMessage,
}: Props) {
  const queryClient = useQueryClient();
  const key = useRef<string | null>(null);
  const mutation = useMutation({
    mutationFn: async () => {
      key.current ??= crypto.randomUUID();
      try {
        const error = await execute(action, targetId, key.current);
        if (error !== null) throw new Error(error);
        key.current = null;
      } catch (error) {
        if (error instanceof TypeError && key.current !== null) {
          const result = await api.GET("/requests/{request_id}", {
            params: {path: {request_id: key.current}},
          });
          if (result.data !== undefined) {
            key.current = null;
            return;
          }
          throw new Error("Checking result; retry with the same operation");
        }
        throw error;
      }
    },
    onSuccess: () => void queryClient.invalidateQueries(),
  });

  function run() {
    if (confirmMessage !== undefined && !window.confirm(confirmMessage)) return;
    mutation.mutate();
  }

  return (
    <span className="command">
      <button disabled={mutation.isPending} onClick={run} type="button">
        {mutation.isPending ? "Submitting…" : children}
      </button>
      {mutation.isError && (
        <span className="error" role="alert">{mutation.error.message}</span>
      )}
    </span>
  );
}

async function execute(
  action: ParticipationAction,
  targetId: string,
  idempotencyKey: string,
): Promise<string | null> {
  const common = {
    params: {
      header: {"Idempotency-Key": idempotencyKey},
      path: {},
    },
    body: {},
  };
  let result;
  switch (action) {
    case "book":
      result = await api.POST("/sessions/{session_id}/book", {
        ...common,
        params: {...common.params, path: {session_id: targetId}},
      });
      break;
    case "waitlist":
      result = await api.POST("/sessions/{session_id}/waitlist", {
        ...common,
        params: {...common.params, path: {session_id: targetId}},
      });
      break;
    case "confirm":
      result = await api.POST("/participations/{participation_id}/confirm", {
        ...common,
        params: {...common.params, path: {participation_id: targetId}},
        body: {simulation_result: "success"},
      });
      break;
    case "cancel":
      result = await api.POST("/participations/{participation_id}/cancel", {
        ...common,
        params: {...common.params, path: {participation_id: targetId}},
      });
      break;
    case "decline":
      result = await api.POST("/participations/{participation_id}/decline", {
        ...common,
        params: {...common.params, path: {participation_id: targetId}},
      });
      break;
    case "leave":
      result = await api.POST("/participations/{participation_id}/leave", {
        ...common,
        params: {...common.params, path: {participation_id: targetId}},
      });
  }
  return result.error?.error.message ?? null;
}
