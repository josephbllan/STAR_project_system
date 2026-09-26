import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SettingRow, api } from "../api";

export function SettingsPage() {
  const client = useQueryClient();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<{ results: SettingRow[] }>("/api/v1/settings/"),
  });
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api("/api/v1/settings/", {
        method: "POST",
        body: JSON.stringify({ key, value: JSON.parse(value || "null"), description: "" }),
      }),
    onSuccess: () => {
      setKey("");
      setValue("");
      client.invalidateQueries({ queryKey: ["settings"] });
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <section className="card">
        <h2 className="text-sm font-bold">Runtime settings</h2>
        <p className="text-[var(--text-muted)]">No credential is stored here.</p>
        <ul className="mt-2">
          {(settings.data?.results ?? []).map((row) => (
            <li key={row.id} className="border-b py-2">
              <p className="font-extrabold">{row.key}</p>
              <pre className="font-mono text-xs">{JSON.stringify(row.value)}</pre>
            </li>
          ))}
          {settings.isError ? <p>Administrator role is required to read settings.</p> : null}
        </ul>
      </section>
      <form className="card space-y-2" onSubmit={onSubmit}>
        <h2 className="text-sm font-bold">Add or replace</h2>
        <input className="field" placeholder="key" value={key} onChange={(e) => setKey(e.target.value)} />
        <textarea className="field" placeholder='JSON value, e.g. 0.5' value={value} onChange={(e) => setValue(e.target.value)} />
        <button type="submit" className="btn btn-primary" disabled={!key}>
          Save
        </button>
      </form>
    </div>
  );
}
