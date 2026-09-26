import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, TaskRow } from "../api";
import { TASKS_QUERY, isInFlight, taskBarPercent, taskProgressLabel } from "../taskProgress";

export function TaskCentre({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: () => api<Pageish>(TASKS_QUERY),
    refetchInterval: 4000,
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api(`/api/v1/tasks/${id}/cancel/`, { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["tasks"] }),
  });
  const rows = tasks.data?.results ?? [];
  return (
    <aside className="fixed right-0 top-0 z-40 flex h-full w-[380px] flex-col border-l bg-white shadow-[0_12px_32px_rgba(17,24,39,0.18)]">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <h2 className="text-sm font-bold">Task Centre</h2>
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      </header>
      <ul className="flex-1 space-y-2 overflow-auto p-4">
        {rows.length === 0 ? <p className="text-[var(--text-muted)]">No task runs.</p> : null}
        {rows.map((task) => (
          <li key={task.public_id} className="card">
            <p className="font-extrabold">{task.task_name}</p>
            <p className="text-xs text-[var(--text-muted)]">
              {task.status}
              {task.phase ? ` · ${task.phase}` : ""}
              {` · ${taskProgressLabel(task)}`}
            </p>
            <div className="mt-2 h-3 overflow-hidden rounded-[10px] border border-[var(--border)]">
              <div
                className="h-full bg-[var(--brand-header)]"
                style={{ width: `${taskBarPercent(task)}%` }}
              />
            </div>
            {isInFlight(task.status) ? (
              <button type="button" className="btn mt-2" onClick={() => cancel.mutate(task.public_id)}>
                Cancel
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </aside>
  );
}

type Pageish = { results: TaskRow[] };
