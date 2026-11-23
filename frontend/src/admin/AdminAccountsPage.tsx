import {useMutation, useQuery, useQueryClient} from "@tanstack/react-query";
import {type FormEvent, useRef, useState} from "react";
import {Navigate} from "react-router-dom";

import {api} from "../api/client";
import type {components} from "../api/schema";
import {useAuth} from "../auth/AuthProvider";

type Account = components["schemas"]["AccountView"];

export function AdminAccountsPage() {
  const {account} = useAuth();
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);
  const query = useQuery({
    queryKey: ["admin", "accounts", cursor],
    queryFn: async () => {
      const {data, error} = await api.GET("/admin/accounts", {
        params: {query: {cursor, limit: 100}},
      });
      if (error !== undefined || data === undefined) throw error;
      return data.data;
    },
    enabled: account?.role === "ADMIN",
  });

  if (account?.role !== "ADMIN") return <Navigate replace to="/" />;
  if (query.isPending) return <p>Loading accounts…</p>;
  if (query.isError) return <p className="error">Unable to load accounts.</p>;

  return (
    <>
      <h1>Account administration</h1>
      <div className="account-list">
        {query.data.items.map((item) => (
          <AccountEditor
            account={item}
            currentAccountId={account.id}
            key={item.id}
          />
        ))}
      </div>
      <div className="pagination">
        <button
          disabled={history.length === 0}
          onClick={() => {
            setCursor(history.at(-1));
            setHistory((items) => items.slice(0, -1));
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

function AccountEditor({
  account,
  currentAccountId,
}: {
  account: Account;
  currentAccountId: string;
}) {
  const queryClient = useQueryClient();
  const key = useRef<string | null>(null);
  const protectedAccount =
    account.id === currentAccountId || account.role === "ADMIN";
  const mutation = useMutation({
    mutationFn: async (form: FormData) => {
      key.current ??= crypto.randomUUID();
      const {error} = await api.PATCH("/admin/accounts/{account_id}", {
        params: {
          header: {"Idempotency-Key": key.current},
          path: {account_id: account.id},
        },
        body: {
          role: String(form.get("role")) as "ATTENDEE" | "ORGANIZER",
          disabled: form.get("disabled") === "on",
          reason: String(form.get("reason")),
        },
      });
      if (error !== undefined) throw new Error(error.error.message);
      key.current = null;
    },
    onSuccess: () => void queryClient.invalidateQueries({
      queryKey: ["admin", "accounts"],
    }),
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!window.confirm("Apply this account and authorization change?")) return;
    mutation.mutate(new FormData(event.currentTarget));
  }

  return (
    <form className="card account-editor" onSubmit={submit}>
      <h2>{account.display_name}</h2>
      <p>{account.email}</p>
      {protectedAccount ? (
        <p>This administrator account cannot be changed here.</p>
      ) : (
        <>
          <label>
            Role
            <select defaultValue={account.role} name="role">
              <option value="ATTENDEE">Attendee</option>
              <option value="ORGANIZER">Organizer</option>
            </select>
          </label>
          <label className="checkbox">
            <input defaultChecked={account.disabled} name="disabled" type="checkbox" />
            Disabled
          </label>
          <label>
            Audit reason
            <textarea maxLength={500} name="reason" required />
          </label>
          <button disabled={mutation.isPending} type="submit">
            {mutation.isPending ? "Saving…" : "Save account"}
          </button>
          {mutation.isError && (
            <span className="error" role="alert">{mutation.error.message}</span>
          )}
        </>
      )}
    </form>
  );
}
