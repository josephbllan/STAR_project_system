import { useQuery } from "@tanstack/react-query";
import { AuditRow, api } from "../api";

export function AuditPage() {
  const events = useQuery({
    queryKey: ["audit"],
    queryFn: () => api<{ results: AuditRow[] }>("/api/v1/audit/"),
  });
  return (
    <div className="space-y-3">
      <p className="rounded border border-[var(--warning)] bg-orange-50 p-3">
        The audit trail is append-only. Events are not edited or deleted.
      </p>
      <p className="rounded border border-[var(--warning)] bg-orange-50 p-3">
        Reading this screen is itself an audited action.
      </p>
      <section className="card overflow-auto">
        {events.isError ? <p>Auditor role is required.</p> : null}
        <table className="w-full text-left">
          <thead className="bg-[var(--surface-muted)] text-[var(--text-secondary)]">
            <tr>
              <th className="p-2">When</th>
              <th className="p-2">Action</th>
              <th className="p-2">Actor</th>
              <th className="p-2">Outcome</th>
              <th className="p-2">Target</th>
            </tr>
          </thead>
          <tbody>
            {(events.data?.results ?? []).map((row) => (
              <tr key={row.public_id} className="border-b">
                <td className="p-2 font-mono text-xs">{row.occurred_at}</td>
                <td className="p-2">{row.action}</td>
                <td className="p-2">
                  {row.actor_username} · {row.actor_role}
                </td>
                <td className="p-2">{row.outcome}</td>
                <td className="p-2 font-mono text-xs">{row.target_type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
