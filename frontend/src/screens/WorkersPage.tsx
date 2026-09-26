import { useQuery } from "@tanstack/react-query";
import { EncoderRow, TaskRow, api } from "../api";

export function WorkersPage() {
  const encoders = useQuery({
    queryKey: ["encoders"],
    queryFn: () => api<{ results: EncoderRow[] }>("/api/v1/encoders/"),
  });
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: () => api<{ results: TaskRow[] }>("/api/v1/tasks/"),
  });
  return (
    <div className="space-y-3">
      <section className="card">
        <h2 className="text-sm font-bold">Encoders</h2>
        <table className="mt-2 w-full text-left">
          <thead className="bg-[var(--surface-muted)] text-[var(--text-secondary)]">
            <tr>
              <th className="p-2">Name</th>
              <th className="p-2">Family</th>
              <th className="p-2">Version</th>
              <th className="p-2">Dimensions</th>
              <th className="p-2">Active</th>
            </tr>
          </thead>
          <tbody>
            {(encoders.data?.results ?? []).map((row) => (
              <tr key={row.id} className="border-b">
                <td className="p-2">{row.name}</td>
                <td className="p-2">{row.family}</td>
                <td className="p-2">{row.version}/{row.preprocess_version}</td>
                <td className="p-2">{row.dimensions}</td>
                <td className="p-2">{row.is_active ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h2 className="text-sm font-bold">Recent worker tasks</h2>
        <ul className="mt-2">
          {(tasks.data?.results ?? []).slice(0, 8).map((task) => (
            <li key={task.public_id} className="py-1">
              {task.task_name} · {task.status}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
