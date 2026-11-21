import type {QueryClient} from "@tanstack/react-query";
import {
  createContext,
  type PropsWithChildren,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {onAccessTokenChange, setAccessToken} from "../api/client";
import {type RealtimeStatus, SeatflowRealtime} from "./SeatflowRealtime";

type RealtimeContextValue = {
  status: RealtimeStatus;
  setChannels: (channels: Iterable<string>) => void;
};

const RealtimeContext = createContext<RealtimeContextValue | null>(null);

export function RealtimeProvider({
  children,
  queryClient,
}: PropsWithChildren<{queryClient: QueryClient}>) {
  const [status, setStatus] = useState<RealtimeStatus>("disconnected");
  const realtime = useMemo(
    () =>
      new SeatflowRealtime(queryClient, setStatus, () => {
        setAccessToken(null);
        void queryClient.invalidateQueries();
      }),
    [queryClient],
  );

  useEffect(() => {
    realtime.start();
    const unsubscribe = onAccessTokenChange(() => {
      realtime.stop();
      realtime.start();
    });
    return () => {
      unsubscribe();
      realtime.stop();
    };
  }, [realtime]);

  const value = useMemo(
    () => ({
      status,
      setChannels: (channels: Iterable<string>) =>
        realtime.setChannels(channels),
    }),
    [realtime, status],
  );
  return (
    <RealtimeContext.Provider value={value}>
      {children}
    </RealtimeContext.Provider>
  );
}

export function useRealtime(): RealtimeContextValue {
  const value = useContext(RealtimeContext);
  if (value === null) {
    throw new Error("useRealtime must be used inside RealtimeProvider");
  }
  return value;
}

export function useRealtimeChannels(channels: Iterable<string>): void {
  const {setChannels} = useRealtime();
  const key = [...channels].sort().join(",");
  useEffect(() => {
    setChannels(key === "" ? [] : key.split(","));
    return () => setChannels([]);
  }, [key, setChannels]);
}
