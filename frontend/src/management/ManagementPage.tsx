import {useMutation, useQuery, useQueryClient} from "@tanstack/react-query";
import {type FormEvent, useRef, useState} from "react";
import {Link, useParams} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {useAuth} from "../auth/AuthProvider";
import {useRealtimeChannels} from "../realtime/RealtimeProvider";

type EventView = components["schemas"]["EventView"];
type SessionView = components["schemas"]["SessionView"];

export function ManagementPage() {
  const {account} = useAuth();
  const query = useQuery({
    queryKey: ["my-events"],
    queryFn: async () => {
      const {data, error} = await api.GET("/me/events", {
        params: {query: {limit: 100}},
      });
      if (error !== undefined || data === undefined) throw error;
      return data.data;
    },
    enabled: account?.role === "ORGANIZER" || account?.role === "ADMIN",
  });

  if (account === null) return <ManagementNotice text="Sign in to manage events." />;
  if (account.role === "ATTENDEE") {
    return <ManagementNotice text="An organizer or administrator role is required." />;
  }
  if (query.isPending) return <p>Loading managed events…</p>;
  if (query.isError) return <ManagementNotice text="Unable to load managed events." />;

  return (
    <>
      <h1>Management</h1>
      <CreateEventForm />
      <h2>Your events</h2>
      <div className="card-grid">
        {query.data.items.map((event) => (
          <article className="card" key={event.id}>
            <h3><Link to={`/management/events/${event.id}`}>{event.title}</Link></h3>
            <p>{event.status}</p>
            <p>{event.location}</p>
          </article>
        ))}
      </div>
    </>
  );
}

export function ManagedEventPage() {
  const {eventId = ""} = useParams();
  const {account} = useAuth();
  const query = useQuery({
    queryKey: ["management", eventId],
    queryFn: async () => {
      const {data, error} = await api.GET("/events/{event_id}", {
        params: {path: {event_id: eventId}},
      });
      if (error !== undefined || data === undefined) throw error;
      return data.data;
    },
    enabled:
      eventId !== "" &&
      account !== null &&
      (account.role === "ORGANIZER" || account.role === "ADMIN"),
  });
  useRealtimeChannels(
    eventId === "" ? [] : [`event:${eventId}:management`],
  );

  if (account === null || account.role === "ATTENDEE") {
    return <ManagementNotice text="Organizer access is required." />;
  }
  if (query.isPending) return <p>Loading event…</p>;
  if (query.isError) return <ManagementNotice text="Unable to load this event." />;
  const event = query.data;

  return (
    <>
      <Link to="/management">← Managed events</Link>
      <h1>{event.title}</h1>
      <p>Status: {event.status}</p>
      <EventEditor event={event} />
      <div className="management-actions">
        {event.status === "DRAFT" && (
          <ManagementButton action="publish-event" targetId={event.id}>
            Publish event
          </ManagementButton>
        )}
        {event.status !== "CANCELLED" && (
          <ManagementButton action="cancel-event" targetId={event.id}>
            Cancel event
          </ManagementButton>
        )}
      </div>
      {event.status === "DRAFT" && <CreateSessionForm eventId={event.id} />}
      <h2>Sessions</h2>
      {event.sessions?.map((session) => (
        <ManagedSession key={session.id} session={session} />
      ))}
    </>
  );
}

function CreateEventForm() {
  const mutation = useManagedMutation(async (key, form: FormData) => {
    return api.POST("/events", {
      params: {header: {"Idempotency-Key": key}},
      body: {
        title: String(form.get("title")),
        description: String(form.get("description")),
        location: String(form.get("location")),
      },
    });
  });
  return (
    <ManagementForm
      legend="Create event"
      mutation={mutation}
      fields={[
        {name: "title", label: "Title"},
        {name: "description", label: "Description", multiline: true},
        {name: "location", label: "Location"},
      ]}
    />
  );
}

function EventEditor({event}: {event: EventView}) {
  const mutation = useManagedMutation(async (key, form: FormData) => {
    return api.PATCH("/events/{event_id}", {
      params: {
        header: {"Idempotency-Key": key},
        path: {event_id: event.id},
      },
      body: {
        title: String(form.get("title")),
        description: String(form.get("description")),
        location: String(form.get("location")),
      },
    });
  });
  return (
    <ManagementForm
      legend="Event details"
      mutation={mutation}
      submitLabel="Save event"
      fields={[
        {name: "title", label: "Title", value: event.title},
        {
          name: "description",
          label: "Description",
          value: event.description,
          multiline: true,
        },
        {name: "location", label: "Location", value: event.location},
      ]}
    />
  );
}

function CreateSessionForm({eventId}: {eventId: string}) {
  const mutation = useManagedMutation(async (key, form: FormData) => {
    return api.POST("/events/{event_id}/sessions", {
      params: {
        header: {"Idempotency-Key": key},
        path: {event_id: eventId},
      },
      body: {
        name: String(form.get("name")),
        time_zone: String(form.get("time_zone")),
        registration_opens_at: toIso(form, "registration_opens_at"),
        registration_closes_at: toIso(form, "registration_closes_at"),
        starts_at: toIso(form, "starts_at"),
        ends_at: toIso(form, "ends_at"),
        capacity: Number(form.get("capacity")),
      },
    });
  });
  return (
    <ManagementForm
      legend="Add session"
      mutation={mutation}
      fields={[
        {name: "name", label: "Name"},
        {
          name: "time_zone",
          label: "IANA time zone",
          value: Intl.DateTimeFormat().resolvedOptions().timeZone,
        },
        {name: "registration_opens_at", label: "Registration opens", type: "datetime-local"},
        {name: "registration_closes_at", label: "Registration closes", type: "datetime-local"},
        {name: "starts_at", label: "Starts", type: "datetime-local"},
        {name: "ends_at", label: "Ends", type: "datetime-local"},
        {name: "capacity", label: "Capacity", type: "number"},
      ]}
    />
  );
}

function ManagedSession({session}: {session: SessionView}) {
  const [showRoster, setShowRoster] = useState(false);
  return (
    <article className="card managed-session">
      <h3>{session.name}</h3>
      <p>Status: {session.status}</p>
      <p>Capacity: {session.capacity}; occupied: {session.occupied_count}</p>
      <CapacityForm session={session} />
      {session.status !== "CANCELLED" && (
        <ManagementButton action="cancel-session" targetId={session.id}>
          Cancel session
        </ManagementButton>
      )}
      <button type="button" onClick={() => setShowRoster((value) => !value)}>
        {showRoster ? "Hide roster" : "View roster"}
      </button>
      {showRoster && <Roster sessionId={session.id} />}
    </article>
  );
}

function CapacityForm({session}: {session: SessionView}) {
  const mutation = useManagedMutation(async (key, form: FormData) => {
    return api.PATCH("/sessions/{session_id}", {
      params: {
        header: {"Idempotency-Key": key},
        path: {session_id: session.id},
      },
      body: {capacity: Number(form.get("capacity"))},
    });
  });
  return (
    <ManagementForm
      legend="Capacity"
      mutation={mutation}
      submitLabel="Update capacity"
      fields={[
        {
          name: "capacity",
          label: "Capacity",
          type: "number",
          value: String(session.capacity),
        },
      ]}
    />
  );
}

function Roster({sessionId}: {sessionId: string}) {
  const query = useQuery({
    queryKey: ["roster", sessionId],
    queryFn: async () => {
      const {data, error} = await api.GET("/sessions/{session_id}/roster", {
        params: {path: {session_id: sessionId}},
      });
      if (error !== undefined || data === undefined) throw error;
      return data.data;
    },
  });
  if (query.isPending) return <p>Loading roster…</p>;
  if (query.isError) return <p className="error">Unable to load roster.</p>;
  return (
    <div className="roster">
      {Object.entries(query.data.counts).map(([state, count]) => (
        <span key={state}>{state}: {count}</span>
      ))}
      {[...query.data.held, ...query.data.offered, ...query.data.confirmed, ...query.data.waitlisted]
        .map((entry) => (
          <div key={entry.participation_id}>
            {entry.display_name} — {entry.state}
            {entry.position !== undefined && ` (#${entry.position})`}
          </div>
        ))}
    </div>
  );
}

type ManagedAction = "publish-event" | "cancel-event" | "cancel-session";

function ManagementButton({
  action,
  targetId,
  children,
}: {
  action: ManagedAction;
  targetId: string;
  children: string;
}) {
  const mutation = useManagedMutation(async (key) => {
    if (action === "publish-event") {
      return api.POST("/events/{event_id}/publish", {
        params: {
          header: {"Idempotency-Key": key},
          path: {event_id: targetId},
        },
      });
    }
    const reason = window.prompt("Required cancellation reason");
    if (reason === null || reason.trim() === "") {
      throw new Error("A cancellation reason is required");
    }
    if (!window.confirm("This cancellation cannot be undone. Continue?")) {
      throw new Error("Cancellation was not submitted");
    }
    return action === "cancel-event"
      ? api.POST("/events/{event_id}/cancel", {
          params: {
            header: {"Idempotency-Key": key},
            path: {event_id: targetId},
          },
          body: {reason},
        })
      : api.POST("/sessions/{session_id}/cancel", {
          params: {
            header: {"Idempotency-Key": key},
            path: {session_id: targetId},
          },
          body: {reason},
        });
  });
  return (
    <span className="command">
      <button
        disabled={mutation.isPending}
        onClick={() => mutation.mutate(new FormData())}
        type="button"
      >
        {mutation.isPending ? "Submitting…" : children}
      </button>
      {mutation.isError && <span className="error">{mutation.error.message}</span>}
    </span>
  );
}

type Field = {
  name: string;
  label: string;
  value?: string;
  type?: string;
  multiline?: boolean;
};

function ManagementForm({
  legend,
  fields,
  mutation,
  submitLabel = "Create",
}: {
  legend: string;
  fields: Field[];
  mutation: ReturnType<typeof useManagedMutation>;
  submitLabel?: string;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate(new FormData(event.currentTarget));
  }
  return (
    <form className="management-form" onSubmit={submit}>
      <fieldset>
        <legend>{legend}</legend>
        {fields.map((field) => (
          <label key={field.name}>
            {field.label}
            {field.multiline ? (
              <textarea defaultValue={field.value} name={field.name} required />
            ) : (
              <input
                defaultValue={field.value}
                min={field.type === "number" ? 1 : undefined}
                name={field.name}
                required
                type={field.type ?? "text"}
              />
            )}
          </label>
        ))}
        <button disabled={mutation.isPending} type="submit">
          {mutation.isPending ? "Submitting…" : submitLabel}
        </button>
        {mutation.isError && <span className="error">{mutation.error.message}</span>}
      </fieldset>
    </form>
  );
}

function useManagedMutation(
  request: (key: string, form: FormData) => Promise<{
    data?: unknown;
    error?: {error: {message: string}};
  }>,
) {
  const queryClient = useQueryClient();
  const key = useRef<string | null>(null);
  return useMutation({
    mutationFn: async (form: FormData) => {
      key.current ??= crypto.randomUUID();
      const result = await request(key.current, form);
      if (result.error !== undefined) throw new Error(result.error.error.message);
      key.current = null;
    },
    onSuccess: () => void queryClient.invalidateQueries(),
  });
}

function ManagementNotice({text}: {text: string}) {
  return <section className="notice"><h1>Management</h1><p>{text}</p></section>;
}

function toIso(form: FormData, name: string): string {
  return new Date(String(form.get(name))).toISOString();
}
