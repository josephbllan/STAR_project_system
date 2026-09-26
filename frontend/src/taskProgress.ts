import { TaskRow } from "./api";

const IN_FLIGHT = new Set(["queued", "started", "running", "retrying"]);

export function isInFlight(status: string | undefined): boolean {
  return Boolean(status && IN_FLIGHT.has(status));
}

export function taskBarPercent(task: TaskRow): number {
  const current = task.progress_current ?? 0;
  const total = task.progress_total;
  if (task.status === "succeeded") return 100;
  if (typeof total === "number" && total > 0) {
    return Math.min(100, (100 * current) / total);
  }
  return 0;
}

export function taskProgressLabel(task: TaskRow): string {
  const current = task.progress_current ?? 0;
  const total = task.progress_total;
  if (typeof total === "number" && total > 0) {
    const pct = Math.round((100 * current) / total);
    return `${current.toLocaleString()} / ${total.toLocaleString()} (${pct}%)`;
  }
  if (task.status === "succeeded") {
    return current ? `${current.toLocaleString()} encoded` : "Complete";
  }
  if (current > 0) return `${current.toLocaleString()} encoded`;
  return "—";
}

export function focusEncodeTask(tasks: TaskRow[]): TaskRow | undefined {
  const corpus = tasks.filter((task) => task.task_name === "indexing.encode_corpus");
  const inFlight = corpus.filter((task) => isInFlight(task.status));
  const ranked = (inFlight.length ? inFlight : corpus).slice().sort((a, b) => {
    const total = (b.progress_total ?? 0) - (a.progress_total ?? 0);
    if (total !== 0) return total;
    return (b.progress_current ?? 0) - (a.progress_current ?? 0);
  });
  return ranked[0] ?? tasks.find((task) => isInFlight(task.status)) ?? tasks[0];
}

export const TASKS_QUERY =
  "/api/v1/tasks/?page_size=100&task_name=indexing.encode_corpus&task_name=indexing.encode_batch&task_name=datasets.scan_mount";
export const ENCODE_CORPUS_QUERY = "/api/v1/tasks/?page_size=20&task_name=indexing.encode_corpus";
