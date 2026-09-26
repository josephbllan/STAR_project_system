import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { CaseRow, QueryRow, ResultRow, RunRow, api, rowsOf } from "../api";
import { StarRating } from "./StarRating";

export function ReviewPage() {
  const client = useQueryClient();
  const [params] = useSearchParams();
  const [tab, setTab] = useState<"hitl" | "agent">("hitl");
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => api<{ results: CaseRow[] }>("/api/v1/cases/?page_size=100") });
  const [caseId, setCaseId] = useState(params.get("case") || "");
  const runs = useQuery({
    queryKey: ["runs", caseId],
    queryFn: () => api<RunRow[]>(`/api/v1/cases/${caseId}/runs/`),
    enabled: Boolean(caseId),
  });
  const [runId, setRunId] = useState(params.get("run") || "");
  const queries = useQuery({
    queryKey: ["queries", runId],
    queryFn: () => api<QueryRow[]>(`/api/v1/runs/${runId}/queries/`),
    enabled: Boolean(runId),
  });
  const [queryId, setQueryId] = useState(params.get("query") || "");
  const results = useQuery({
    queryKey: ["results", queryId],
    queryFn: () => api<ResultRow[]>(`/api/v1/queries/${queryId}/results/`),
    enabled: Boolean(queryId),
  });
  const [selected, setSelected] = useState<ResultRow | null>(null);
  const [note, setNote] = useState("");
  const rate = useMutation({
    mutationFn: (value: number) =>
      api("/api/v1/review/ratings/", {
        method: "POST",
        body: JSON.stringify({ result_public_id: selected?.public_id, value, scope: "result" }),
      }),
  });
  const saveNote = useMutation({
    mutationFn: () =>
      api("/api/v1/review/notes/", {
        method: "POST",
        body: JSON.stringify({ result_public_id: selected?.public_id, body: note, scope: "result" }),
      }),
    onSuccess: () => setNote(""),
  });
  const approve = useMutation({
    mutationFn: () =>
      api("/api/v1/review/approvals/", {
        method: "POST",
        body: JSON.stringify({ result_public_id: selected?.public_id }),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["results", queryId] }),
  });

  return (
    <div className="space-y-3">
      <div className="inline-flex gap-1 rounded-xl border bg-[var(--surface-muted)] p-1.5">
        <button type="button" className={tab === "hitl" ? "btn" : "btn opacity-60"} onClick={() => setTab("hitl")}>
          Human-in-the-Loop
        </button>
        <button type="button" className={tab === "agent" ? "btn" : "btn opacity-60"} onClick={() => setTab("agent")}>
          Agentic AI
        </button>
      </div>
      {tab === "agent" ? (
        <section className="card">
          <p>Agentic review is specified for release 1 as a read of existing notes. Select a result in Human-in-the-Loop to add a note there.</p>
        </section>
      ) : (
        <div className="grid gap-3 lg:grid-cols-[280px_1fr_280px]">
          <section className="card space-y-2">
            <label>
              Case
              <select className="field mt-1" value={caseId} onChange={(e) => setCaseId(e.target.value)}>
                <option value="">Select</option>
                {(cases.data?.results ?? []).map((row) => (
                  <option key={row.public_id} value={row.public_id}>
                    {row.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Run
              <select className="field mt-1" value={runId} onChange={(e) => setRunId(e.target.value)}>
                <option value="">Select</option>
                {(runs.data ?? []).map((row) => (
                  <option key={row.public_id} value={row.public_id}>
                    {row.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Query
              <select className="field mt-1" value={queryId} onChange={(e) => setQueryId(e.target.value)}>
                <option value="">Select</option>
                {rowsOf(queries.data).map((row) => (
                    <option key={row.public_id} value={row.public_id}>
                      Q{row.sequence} · {row.probe_filename || row.query_text || row.query_type}
                    </option>
                  ))}
              </select>
            </label>
          </section>
          <section className="card">
            <h2 className="text-sm font-bold">Results</h2>
            <ul>
              {(results.data ?? []).map((row) => (
                <li key={row.public_id}>
                  <button
                    type="button"
                    className="flex w-full justify-between rounded-[10px] px-2 py-2 text-left"
                    style={{ background: selected?.public_id === row.public_id ? "var(--select-blue)" : undefined }}
                    onClick={() => setSelected(row)}
                  >
                    <span>#{row.rank}</span>
                    <span className="font-mono">confidence={Number(row.score_fused ?? 0).toFixed(3)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <section className="card space-y-3">
            <h2 className="text-sm font-bold">Attribution</h2>
            {selected ? (
              <>
                <StarRating value={0} onChange={(value) => value && rate.mutate(value)} />
                <textarea className="field" rows={4} value={note} onChange={(e) => setNote(e.target.value)} />
                <button type="button" className="btn" onClick={() => saveNote.mutate()}>
                  Save note
                </button>
                <button type="button" className="btn btn-primary" onClick={() => approve.mutate()}>
                  Request approval
                </button>
              </>
            ) : (
              <p className="text-[var(--text-muted)]">Select a result.</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
