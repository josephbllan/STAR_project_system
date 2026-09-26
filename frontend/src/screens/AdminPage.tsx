import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SessionUser, api } from "../api";

const ROLES = ["administrator", "investigator", "analyst", "reviewer", "auditor"];

export function AdminPage() {
  const client = useQueryClient();
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => api<{ results: SessionUser[] }>("/api/v1/users/"),
  });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("analyst");
  const create = useMutation({
    mutationFn: () =>
      api("/api/v1/users/", {
        method: "POST",
        body: JSON.stringify({ username, password, role }),
      }),
    onSuccess: () => {
      setUsername("");
      setPassword("");
      client.invalidateQueries({ queryKey: ["users"] });
    },
  });
  const deactivate = useMutation({
    mutationFn: (id: string) => api(`/api/v1/users/${id}/deactivate/`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["users"] }),
  });
  const reset = useMutation({
    mutationFn: ({ id, next }: { id: string; next: string }) =>
      api(`/api/v1/users/${id}/password/`, { method: "POST", body: JSON.stringify({ password: next }) }),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <div className="grid gap-3 lg:grid-cols-[320px_1fr]">
      <form className="card space-y-2" onSubmit={onSubmit}>
        <h2 className="text-sm font-bold">Create account</h2>
        <input className="field" placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
        <input className="field" type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <select className="field" value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
        <button type="submit" className="btn btn-primary">
          Create
        </button>
      </form>
      <section className="card">
        <h2 className="text-sm font-bold">Accounts</h2>
        {users.isError ? <p>Administrator role is required.</p> : null}
        <ul>
          {(users.data?.results ?? []).map((user) => (
            <li key={user.public_id} className="flex items-center justify-between border-b py-2">
              <span>
                {user.username} · {user.role}
                {user.is_active ? "" : " (deactivated)"}
              </span>
              <span className="flex gap-2">
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    const next = window.prompt("New password");
                    if (next) reset.mutate({ id: user.public_id, next });
                  }}
                >
                  Reset password
                </button>
                <button type="button" className="btn btn-danger" onClick={() => deactivate.mutate(user.public_id)}>
                  Deactivate
                </button>
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
