import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { CaseRow, ReportRow, RunRow, api } from "../api";

export function CasesPage() {
  const { id } = useParams();
  if (id) return <CaseDetail id={id} />;
  return <CaseList />;
}

function CaseList() {
  const client = useQueryClient();
  const cases = useQuery({ queryKey: ["cases"], queryFn: () => api<{ results: CaseRow[] }>("/api/v1/cases/?page_size=100") });
  const [name, setName] = useState("");
  const [reference, setReference] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api<CaseRow>("/api/v1/cases/", {
        method: "POST",
        body: JSON.stringify({ name, reference }),
      }),
    onSuccess: () => {
      setName("");
      client.invalidateQueries({ queryKey: ["cases"] });
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <div className="grid gap-3 lg:grid-cols-[320px_1fr]">
      <form className="card space-y-2" onSubmit={onSubmit}>
        <h2 className="text-sm font-bold">Open case</h2>
        <input className="field" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <input className="field" placeholder="Reference" value={reference} onChange={(e) => setReference(e.target.value)} />
        <button type="submit" className="btn btn-primary" disabled={!name || create.isPending}>
          Create
        </button>
        {create.isError ? <p role="alert">Could not create the case. Investigator role is required.</p> : null}
      </form>
      <section className="card">
        <h2 className="text-sm font-bold">Sessions</h2>
        <ul className="mt-2">
          {(cases.data?.results ?? []).map((row) => (
            <li key={row.public_id} className="flex items-center gap-3 rounded-[10px] px-2 py-2 even:bg-[var(--surface-muted)]">
              <span className="flex h-11 w-11 items-center justify-center rounded-full bg-[#dbeafe] font-extrabold">C</span>
              <Link to={`/cases/${row.public_id}`} className="min-w-0 flex-1 font-extrabold text-[var(--brand-indigo)]">
                {row.name}
              </Link>
              <span className="text-xs text-[var(--text-muted)]">{row.status}</span>
              <Link className="btn px-2" to={`/search?case=${row.public_id}`}>
                Load to Search
              </Link>
            </li>
          ))}
          {!cases.data?.results.length ? (
            <p className="py-8 text-center">No cases in your scope.</p>
          ) : null}
        </ul>
      </section>
    </div>
  );
}

function CaseDetail({ id }: { id: string }) {
  const client = useQueryClient();
  const detail = useQuery({ queryKey: ["case", id], queryFn: () => api<CaseRow>(`/api/v1/cases/${id}/`) });
  const runs = useQuery({ queryKey: ["runs", id], queryFn: () => api<RunRow[]>(`/api/v1/cases/${id}/runs/`) });
  const close = useMutation({
    mutationFn: () => api(`/api/v1/cases/${id}/status/`, { method: "POST", body: JSON.stringify({ status: "closed" }) }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["case", id] }),
  });
  const report = useMutation({
    mutationFn: () =>
      api<ReportRow>("/api/v1/reports/", {
        method: "POST",
        body: JSON.stringify({ case_public_id: id, format: "pdf" }),
      }),
  });
  const row = detail.data;
  if (!row) return <p>Loading…</p>;
  return (
    <div className="space-y-3">
      <section className="card">
        <h2 className="text-sm font-bold">{row.name}</h2>
        <p className="text-[var(--text-muted)]">{row.reference || "No reference"} · {row.status}</p>
        <p>{row.description}</p>
        <div className="mt-3 flex gap-2">
          <button type="button" className="btn btn-danger" onClick={() => close.mutate()}>
            Close Case
          </button>
          <button type="button" className="btn" onClick={() => report.mutate()}>
            Request PDF
          </button>
          <Link
            className="btn btn-primary"
            to={
              (runs.data ?? []).find((run) => run.saved)?.public_id
                ? `/search?case=${id}&run=${(runs.data ?? []).find((run) => run.saved)?.public_id}`
                : `/search?case=${id}`
            }
          >
            Load to Search
          </Link>
          <Link className="btn" to={`/review?case=${id}`}>
            Review this case
          </Link>
        </div>
        {report.data ? <p className="mt-2">Report {report.data.public_id} · {report.data.status}</p> : null}
      </section>
      <section className="card">
        <h2 className="text-sm font-bold">Runs</h2>
        <ul>
          {(runs.data ?? []).map((run) => (
            <li key={run.public_id} className="flex items-center gap-2 py-1">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#dcfce7] text-xs font-extrabold">
                R
              </span>
              <span className="flex-1">
                {run.label} · K={run.top_k} · λ={Number(run.model_weight).toFixed(2)}
                {run.saved ? " · saved" : ""}
              </span>
              {run.saved ? (
                <Link className="btn px-2" to={`/search?case=${id}&run=${run.public_id}`}>
                  Load to Search
                </Link>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
