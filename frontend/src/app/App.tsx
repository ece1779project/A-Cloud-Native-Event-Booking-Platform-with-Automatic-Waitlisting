import {useQuery, useQueryClient} from "@tanstack/react-query";
import {type FormEvent, useState} from "react";
import {
  Link,
  Navigate,
  NavLink,
  Route,
  Routes,
  useParams,
} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {AdminAccountsPage} from "../admin/AdminAccountsPage";
import {LoginForm, useAuth} from "../auth/AuthProvider";
import {
  ManagedEventPage,
  ManagementPage,
} from "../management/ManagementPage";
import {CommandButton} from "../participations/CommandButton";
import {
  useRealtime,
  useRealtimeChannels,
} from "../realtime/RealtimeProvider";
import {DeadlineCountdown} from "../time/DeadlineCountdown";

const DISCONNECTED_POLL_MS = 10_000;

export function App() {
  const {status} = useRealtime();
  const {account, logout} = useAuth();
  return (
    <div className="app-shell">
      <header>
        <Link className="brand" to="/">Ticketbooking</Link>
        <nav aria-label="Primary navigation">
          <NavLink to="/">Events</NavLink>
          <NavLink to="/registrations">My registrations</NavLink>
          <NavLink to="/management">Management</NavLink>
          {account?.role === "ADMIN" && (
            <NavLink to="/admin/accounts">Accounts</NavLink>
          )}
        </nav>
        <span className={`connection connection--${status}`}>
          {status === "connected"
            ? "Live updates connected"
            : "Real-time connection temporarily unavailable"}
        </span>
        {account === null ? (
          <Link to="/login">Sign in</Link>
        ) : (
          <div className="account-menu">
            <span>{account.display_name}</span>
            <button type="button" onClick={() => void logout()}>Sign out</button>
          </div>
        )}
      </header>
      <main>
        <Routes>
          <Route path="/" element={<EventList />} />
          <Route path="/events/:eventId" element={<EventDetails />} />
          <Route path="/registrations" element={<MyRegistrations />} />
          <Route path="/management" element={<ManagementPage />} />
          <Route path="/management/events/:eventId" element={<ManagedEventPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/admin/accounts" element={<AdminAccountsPage />} />
        </Routes>
      </main>
    </div>
  );
}

function LoginPage() {
  const {account, loading} = useAuth();
  if (loading) return <p>Checking session…</p>;
  return account === null ? <LoginForm /> : <Navigate replace to="/" />;
}

function EventList() {
  const {status} = useRealtime();
  const [search, setSearch] = useState("");
  const [openOnly, setOpenOnly] = useState(false);
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);
  const query = useQuery({
    queryKey: ["events", search, openOnly, cursor],
    queryFn: async () => {
      const {data, error} = await api.GET("/events", {
        params: {
          query: {
            q: search === "" ? undefined : search,
            open: openOnly,
            cursor,
            limit: 20,
          },
        },
      });
      if (error !== undefined || data === undefined) throw error;
      return data.data;
    },
    refetchInterval: status === "connected" ? false : DISCONNECTED_POLL_MS,
  });

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSearch(String(form.get("q")).trim());
    setOpenOnly(form.get("open") === "on");
    setCursor(undefined);
    setHistory([]);
  }

  if (query.isPending) return <p>Loading events…</p>;
  if (query.isError) return <Unavailable />;
  return (
    <>
      <h1>Upcoming events</h1>
      <form className="event-search" onSubmit={submitSearch}>
        <label>
          Search
          <input defaultValue={search} maxLength={100} name="q" type="search" />
        </label>
        <label className="checkbox">
          <input defaultChecked={openOnly} name="open" type="checkbox" />
          Registration open
        </label>
        <button type="submit">Apply filters</button>
      </form>
      <div className="card-grid">
        {query.data.items.map((event) => (
          <article className="card" key={event.id}>
            <h2><Link to={`/events/${event.id}`}>{event.title}</Link></h2>
            <p>{event.description}</p>
            <dl>
              <div><dt>Location</dt><dd>{event.location}</dd></div>
              <div>
                <dt>Next session</dt>
                <dd>{formatDate(event.next_session_starts_at)}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
      <div className="pagination">
        <button
          disabled={history.length === 0}
          onClick={() => {
            const previous = history.at(-1);
            setHistory((items) => items.slice(0, -1));
            setCursor(previous);
          }}
          type="button"
        >
          Previous
        </button>
        <button
          disabled={query.data.next_cursor === null}
          onClick={() => {
            setHistory((items) => [...items, cursor]);
            setCursor(query.data.next_cursor ?? undefined);
          }}
          type="button"
        >
          Next
        </button>
      </div>
    </>
  );
}

function EventDetails() {
  const {eventId = ""} = useParams();
  const {status} = useRealtime();
  const query = useQuery({
    queryKey: ["event", eventId],
    queryFn: async () => {
      const {data, error} = await api.GET("/events/{event_id}", {
        params: {path: {event_id: eventId}},
      });
      if (error !== undefined || data === undefined) throw error;
      return data.data;
    },
    enabled: eventId !== "",
    refetchInterval: status === "connected" ? false : DISCONNECTED_POLL_MS,
  });
  const sessionChannels =
    query.data?.sessions?.map((session) => `session:${session.id}`) ?? [];
  useRealtimeChannels(sessionChannels);

  if (query.isPending) return <p>Loading event…</p>;
  if (query.isError) return <Unavailable />;
  return (
    <>
      <Link to="/">← All events</Link>
      <h1>{query.data.title}</h1>
      <p>{query.data.description}</p>
      <p><strong>Location:</strong> {query.data.location}</p>
      <h2>Sessions</h2>
      <div className="card-grid">
        {query.data.sessions?.map((session) => (
          <SessionCard key={session.id} session={session} />
        ))}
      </div>
    </>
  );
}

function SessionCard({
  session,
}: {
  session: components["schemas"]["SessionView"];
}) {
  const {account} = useAuth();
  const participation = useQuery({
    queryKey: ["my-participation", session.id],
    queryFn: async () => {
      const {data, response} = await api.GET(
        "/sessions/{session_id}/my-participation",
        {params: {path: {session_id: session.id}}},
      );
      if (response.status === 404) return null;
      if (data === undefined) throw new Error("Unable to load registration");
      return data.data;
    },
    enabled: account !== null,
  });
  const current = participation.data;
  const cancelled = session.status === "CANCELLED";

  return (
    <article className="card">
      <h3>{session.name}</h3>
      <p>{formatDate(session.starts_at)}</p>
      {cancelled ? (
        <p>
          Session cancelled
          {session.cancellation_reason ? `: ${session.cancellation_reason}` : ""}
        </p>
      ) : (
        <p>{session.available_count} seats available</p>
      )}
      {!cancelled && (account === null ? (
        <Link to="/login">Sign in to register</Link>
      ) : participation.isPending ? (
        <span>Checking registration…</span>
      ) : current === null || current === undefined || current.historical ? (
        session.available_count > 0 && session.waitlist_count === 0 ? (
          <CommandButton action="book" targetId={session.id}>Book seat</CommandButton>
        ) : (
          <CommandButton action="waitlist" targetId={session.id}>Join waitlist</CommandButton>
        )
      ) : (
        <ParticipationActions participation={current} />
      ))}
    </article>
  );
}

function ParticipationActions({
  participation,
}: {
  participation: components["schemas"]["ParticipationView"];
}) {
  const queryClient = useQueryClient();
  return (
    <div className="participation-actions">
      <p>Status: {participation.state}</p>
      {(participation.state === "HELD" || participation.state === "OFFERED") &&
        participation.deadline_at !== null && (
          <p>
            Respond by {formatDate(participation.deadline_at)} —{" "}
            <DeadlineCountdown
              deadline={participation.deadline_at}
              onExpired={() => void queryClient.invalidateQueries()}
            />
          </p>
        )}
      {participation.queue_position !== null && (
        <p>Currently number {participation.queue_position}; a seat is not guaranteed.</p>
      )}
      {(participation.state === "HELD" || participation.state === "OFFERED") && (
        <>
          <p>Simulated payment; no actual charge</p>
          <CommandButton action="confirm" targetId={participation.id}>
            Confirm seat
          </CommandButton>
        </>
      )}
      {(participation.state === "HELD" || participation.state === "CONFIRMED") && (
        <CommandButton
          action="cancel"
          targetId={participation.id}
          confirmMessage="Cancel this registration?"
        >
          Cancel registration
        </CommandButton>
      )}
      {participation.state === "OFFERED" && (
        <CommandButton
          action="decline"
          targetId={participation.id}
          confirmMessage="Decline this seat offer?"
        >
          Decline offer
        </CommandButton>
      )}
      {participation.state === "WAITLISTED" && (
        <CommandButton
          action="leave"
          targetId={participation.id}
          confirmMessage="Leave this waitlist?"
        >
          Leave waitlist
        </CommandButton>
      )}
    </div>
  );
}

function MyRegistrations() {
  const {status} = useRealtime();
  const {account} = useAuth();
  useRealtimeChannels(["me:participations"]);
  const query = useQuery({
    queryKey: ["my-participations"],
    queryFn: async () => {
      const {data, error} = await api.GET("/me/participations", {
        params: {query: {limit: 100}},
      });
      if (error !== undefined || data === undefined) throw error;
      return data.data;
    },
    enabled: account !== null,
    refetchInterval: status === "connected" ? false : DISCONNECTED_POLL_MS,
  });
  if (account === null) return <Unavailable authenticated />;
  if (query.isPending) return <p>Loading registrations…</p>;
  if (query.isError) return <Unavailable authenticated />;
  return (
    <>
      <h1>My registrations</h1>
      {query.data.items.map((participation) => (
        <article className="card" key={participation.id}>
          <h2>{participation.session.name}</h2>
          {participation.session.status === "CANCELLED" &&
            participation.session.event_status !== "CANCELLED" && (
              <p>Session cancelled</p>
            )}
          {participation.display_label != null && (
            <p>{participation.display_label}</p>
          )}
          {participation.historical ? (
            <p>Status: {participation.state}</p>
          ) : (
            <ParticipationActions participation={participation} />
          )}
        </article>
      ))}
    </>
  );
}

function Unavailable({authenticated = false}: {authenticated?: boolean}) {
  return (
    <section className="notice" role="alert">
      <h1>{authenticated ? "Sign in required" : "Unable to load data"}</h1>
      <p>Please retry after the API is available.</p>
    </section>
  );
}

function formatDate(value: string | null | undefined): string {
  return value === null || value === undefined
    ? "To be announced"
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value));
}
