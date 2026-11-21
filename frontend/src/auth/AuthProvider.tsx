import {
  createContext,
  type FormEvent,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import {api, setAccessToken} from "../api/client";
import type {components} from "../api/schema";

type Account = components["schemas"]["AccountView"];
type AuthContextValue = {
  account: Account | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<string | null>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);
const REFRESH_EARLY_MS = 30_000;
let refreshInFlight: Promise<components["schemas"]["TokenResponse"] | null> | null =
  null;

export function AuthProvider({children}: PropsWithChildren) {
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const refreshTimer = useRef<number | null>(null);

  const clearSession = useCallback(() => {
    if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
    refreshTimer.current = null;
    setAccessToken(null);
    setAccount(null);
  }, []);

  const acceptToken = useCallback(
    (data: components["schemas"]["TokenResponse"]) => {
      if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
      setAccessToken(data.access_token);
      setAccount(data.account);
      refreshTimer.current = window.setTimeout(
        () => void refresh(),
        Math.max(1000, data.expires_in * 1000 - REFRESH_EARLY_MS),
      );
    },
    // ``refresh`` is stable after initial render and is read when the timer fires.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const refresh = useCallback(async () => {
    const token = await requestRefresh();
    if (token === null) {
      clearSession();
    } else {
      acceptToken(token);
    }
    setLoading(false);
  }, [acceptToken, clearSession]);

  useEffect(() => {
    void refresh();
    return clearSession;
  }, [clearSession, refresh]);

  const login = useCallback(
    async (email: string, password: string) => {
      try {
        const {data, error} = await api.POST("/auth/login", {
          body: {email, password},
        });
        if (error !== undefined || data === undefined) {
          return error?.error.message ?? "Unable to sign in";
        }
        acceptToken(data.data);
        return null;
      } catch {
        return "Unable to reach the API";
      }
    },
    [acceptToken],
  );

  const logout = useCallback(async () => {
    const csrf = readCookie("csrf_token");
    try {
      if (csrf !== null) {
        await api.POST("/auth/logout", {
          params: {header: {"X-CSRF-Token": csrf}},
        });
      }
    } catch {
      // Local credentials are cleared even if the server cannot be reached.
    } finally {
      clearSession();
    }
  }, [clearSession]);

  return (
    <AuthContext.Provider value={{account, loading, login, logout}}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

export function LoginForm() {
  const {login} = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    const form = new FormData(event.currentTarget);
    setError(
      await login(String(form.get("email")), String(form.get("password"))),
    );
    setSubmitting(false);
  }

  return (
    <form className="auth-form" onSubmit={submit}>
      <h1>Sign in</h1>
      <label>Email<input name="email" type="email" autoComplete="email" required /></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
      {error !== null && <p className="error" role="alert">{error}</p>}
      <button disabled={submitting} type="submit">
        {submitting ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return cookie === undefined
    ? null
    : decodeURIComponent(cookie.slice(prefix.length));
}

function requestRefresh(): Promise<components["schemas"]["TokenResponse"] | null> {
  if (refreshInFlight !== null) return refreshInFlight;
  const csrf = readCookie("csrf_token");
  if (csrf === null) return Promise.resolve(null);

  refreshInFlight = (async () => {
    try {
      const {data, error} = await api.POST("/auth/refresh", {
        params: {header: {"X-CSRF-Token": csrf}},
      });
      return error === undefined && data !== undefined ? data.data : null;
    } catch {
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}
