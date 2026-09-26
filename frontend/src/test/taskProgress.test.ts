import { describe, expect, it } from "vitest";
import { focusEncodeTask } from "../taskProgress";
import type { TaskRow } from "../api";

function task(partial: Partial<TaskRow>): TaskRow {
  return {
    public_id: "x",
    task_name: "indexing.encode_corpus",
    status: "started",
    phase: "dispatching",
    progress_current: 0,
    progress_total: 0,
    queued_at: "",
    started_at: "",
    finished_at: null,
    ...partial,
  };
}

describe("focusEncodeTask", () => {
  it("prefers the in-flight encode_corpus with the largest total", () => {
    const focus = focusEncodeTask([
      task({ public_id: "tiny", progress_current: 0, progress_total: 2 }),
      task({ public_id: "ndf", progress_current: 1200, progress_total: 8674 }),
      task({
        public_id: "batch",
        task_name: "indexing.encode_batch",
        progress_current: 0,
        progress_total: 32,
      }),
    ]);
    expect(focus?.public_id).toBe("ndf");
  });
});
