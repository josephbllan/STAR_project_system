import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CorpusRow, EvidenceRow, RecallRow, TaskRow, api } from "../api";
import { SignedThumb } from "./SignedThumb";
import {
  ENCODE_CORPUS_QUERY,
  TASKS_QUERY,
  focusEncodeTask,
  isInFlight,
  taskBarPercent,
  taskProgressLabel,
} from "../taskProgress";

const STAGES = [
  ["Discover", "Scan folders"],
  ["Prepare", "Load models"],
  ["Dedup", "SHA‑256 unique"],
  ["Encode", "CLIP/DINO vectors"],
  ["Add", "pgvector"],
  ["Persist", "DB + manifests"],
] as const;

const CHECKLIST = [
  "Scan mounted folders",
  "Prepare model/index registry",
  "Deduplicate by SHA-256",
  "Encode images (CLIP/DINO)",
  "Add vectors to pgvector",
  "Persist index and metadata",
  "Update statistics",
];

function formatRecall(row: RecallRow): string {
  if (row.measured_recall == null || row.recall_k == null || !row.measured_at) {
    return "Not measured. Approximate search has not been validated for this corpus.";
  }
  const date = new Date(row.measured_at);
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  return `Recall@${row.recall_k}: ${Number(row.measured_recall).toFixed(3)} (measured ${day}/${month}, m=${row.m ?? "—"}, ef_construction=${row.ef_construction ?? "—"}, ef_search=${row.ef_search ?? "—"})`;
}

export function IndexPage() {
  const client = useQueryClient();
  const corpora = useQuery({
    queryKey: ["corpora"],
    queryFn: () => api<{ results: CorpusRow[] }>("/api/v1/corpora/?page_size=100"),
  });
  const coverage = useQuery({
    queryKey: ["corpora", "coverage"],
    queryFn: () => api<{ results: CorpusRow[] }>("/api/v1/corpora/coverage/"),
    refetchInterval: 8000,
  });
  const recall = useQuery({
    queryKey: ["encoders", "recall"],
    queryFn: () => api<{ results: RecallRow[] }>("/api/v1/encoders/recall/"),
  });
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: () => api<{ results: TaskRow[] }>(TASKS_QUERY),
    refetchInterval: 4000,
  });
  const corpusTasks = useQuery({
    queryKey: ["tasks", "encode_corpus"],
    queryFn: () => api<{ results: TaskRow[] }>(ENCODE_CORPUS_QUERY),
    refetchInterval: 4000,
  });
  const [models, setModels] = useState<string[]>(["CLIP", "DINO"]);
  const [selected, setSelected] = useState<number[]>([]);
  const [batchSize, setBatchSize] = useState("64");
  const [message, setMessage] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewCorpus, setPreviewCorpus] = useState("");
  const [pausing, setPausing] = useState(false);
  const [showLog, setShowLog] = useState(true);
  const logRef = useRef<HTMLPreElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const rows = corpora.data?.results ?? [];
    if (rows.length && selected.length === 0) setSelected(rows.map((row) => row.id));
  }, [corpora.data, selected.length]);

  const encodeTasks = (tasks.data?.results ?? []).filter((task) =>
    ["indexing.encode_corpus", "indexing.encode_batch", "datasets.scan_mount"].includes(task.task_name),
  );
  const parentTasks = corpusTasks.data?.results ?? encodeTasks.filter((task) => task.task_name === "indexing.encode_corpus");
  const active = [...parentTasks, ...encodeTasks].filter((task) => isInFlight(task.status));
  const focus = focusEncodeTask(parentTasks.length ? parentTasks : encodeTasks);
  const latestCorpus = parentTasks.find((task) => isInFlight(task.status)) ?? parentTasks[0];
  const done = latestCorpus?.status === "succeeded" && !active.length;
  const progressed = (focus?.progress_current ?? 0) > 0;
  const activeStage = !focus
    ? -1
    : isInFlight(focus.status) && (focus.phase === "enumerating" || focus.phase === "planning") && !progressed
      ? 0
      : isInFlight(focus.status) &&
          (focus.phase === "loading" || focus.phase === "dispatching") &&
          !progressed
        ? 1
        : focus.phase === "hashing"
          ? 2
          : focus.phase === "encoding" || (isInFlight(focus.status) && progressed)
            ? 3
            : focus.phase === "persisting"
              ? 4
              : focus.status === "succeeded"
                ? 5
                : 3;

  const logs = useMemo(
    () =>
      encodeTasks
        .filter((task) => !(task.status === "failed" && (task.progress_current ?? 0) === 0))
        .slice()
        .reverse()
        .map((task) => {
          const elapsed = task.started_at
            ? formatElapsed(Date.now() - new Date(task.started_at).getTime())
            : "00:00:00";
          return `${task.task_name} · ${task.status}${task.phase ? ` · ${task.phase}` : ""} · ${taskProgressLabel(task)} · ${elapsed} elapsed`;
        }),
    [encodeTasks],
  );

  useEffect(() => {
    if (showLog && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs, showLog]);

  const index = useMutation({
    mutationFn: async () => {
      const responses = await Promise.all(
        selected.map((id) =>
          api<{ runs: { public_id: string; created?: boolean }[]; already_in_progress?: boolean }>(
            `/api/v1/corpora/${id}/encode/`,
            { method: "POST" },
          ),
        ),
      );
      return responses;
    },
    onSuccess: (responses) => {
      progressRef.current?.scrollIntoView({ behavior: "smooth" });
      if (responses.every((row) => row.already_in_progress)) {
        setMessage("That indexing run is already in progress.");
      } else {
        const n = responses.reduce((sum, row) => sum + row.runs.length, 0);
        setMessage(n ? `Indexing started (${n} encode tasks).` : "No active encoders to run.");
      }
      client.invalidateQueries({ queryKey: ["tasks"] });
      client.invalidateQueries({ queryKey: ["corpora"] });
      client.invalidateQueries({ queryKey: ["corpora", "coverage"] });
    },
    onError: () => setMessage("Indexing failed."),
  });

  const pause = useMutation({
    mutationFn: async () => {
      setPausing(true);
      await Promise.all(active.map((task) => api(`/api/v1/tasks/${task.public_id}/cancel/`, { method: "POST" })));
    },
    onSuccess: () => {
      setMessage("Indexing paused");
      client.invalidateQueries({ queryKey: ["tasks"] });
    },
    onSettled: () => setPausing(false),
  });

  function toggleModel(name: string) {
    setModels((current) => (current.includes(name) ? current.filter((item) => item !== name) : [...current, name]));
  }
  function toggleCorpus(id: number) {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  const coverageRows = mergeCoverage(coverage.data?.results, corpora.data?.results);
  const ndfsim = coverageRows.find((row) => row.code === "NDFsim" || row.name === "NDFsim");
  const imagesIndexed =
    !!ndfsim &&
    (ndfsim.evidence_count ?? 0) > 0 &&
    (ndfsim.indexed_count ?? 0) >= (ndfsim.evidence_count ?? 0);
  const progressCaption = imagesIndexed
    ? isInFlight(focus?.status)
      ? `All NDFsim images are indexed (${(ndfsim?.indexed_count ?? 0).toLocaleString()} / ${(ndfsim?.evidence_count ?? 0).toLocaleString()}). A second model is still encoding ${taskProgressLabel(focus!)}.`
      : `All NDFsim images are indexed (${(ndfsim?.indexed_count ?? 0).toLocaleString()} / ${(ndfsim?.evidence_count ?? 0).toLocaleString()}).`
    : focus
      ? `Encoding ${taskProgressLabel(focus)} · ${focus.phase || focus.status} · ${
          focus.started_at ? formatElapsed(Date.now() - new Date(focus.started_at).getTime()) : "00:00:00"
        } elapsed`
      : "Idle";

  return (
    <div className="space-y-3 p-2">
      <section className="card space-y-3">
        <h2 className="text-sm font-bold">Index options (what gets indexed)</h2>
        <div>
          <p className="mb-1 font-bold">Models:</p>
          <div className="flex flex-wrap gap-2">
            {["CLIP", "DINO"].map((name) => (
              <button
                key={name}
                type="button"
                className={`chip ${models.includes(name) ? "chip-on" : ""}`}
                onClick={() => toggleModel(name)}
              >
                {name}
              </button>
            ))}
          </div>
        </div>
        <div>
          <p className="mb-1 font-bold">Corpora:</p>
          <div className="flex flex-wrap gap-2">
            {(corpora.data?.results ?? []).map((row) => (
              <button
                key={row.id}
                type="button"
                className={`chip ${selected.includes(row.id) ? "chip-on" : ""}`}
                onClick={() => toggleCorpus(row.id)}
              >
                {row.name}
              </button>
            ))}
          </div>
        </div>
        <label className="block max-w-xs">
          Device:
          <select className="field mt-1" value="Auto" disabled>
            <option>Auto</option>
            <option>CPU</option>
          </select>
          <span className="mt-1 block text-xs text-[var(--text-muted)]">Set by the worker deployment.</span>
        </label>
        <label className="block max-w-xs">
          Batch size:
          <input
            className="field mt-1"
            type="number"
            min={1}
            max={4096}
            value={batchSize}
            onChange={(e) => setBatchSize(e.target.value)}
          />
        </label>
        <label className="flex items-center gap-2" title="Histogram equalisation is always enabled to match search settings.">
          <input type="checkbox" checked disabled />
          IR Equalisation (always on)
        </label>
        <p className="text-xs text-[var(--text-muted)]">
          Tip: Indexing is append-only and deduplicated (SHA‑256). Re-indexing usually only processes new files.
        </p>
        <p className="text-xs text-[var(--text-muted)]">
          Content already encoded is not re-encoded, including where the same image is registered in a second corpus.
        </p>
      </section>

      <section className="card space-y-3">
        <h2 className="text-sm font-bold">Indexing flow (what happens when you press Start)</h2>
        <div className="flex flex-wrap items-stretch gap-2">
          {STAGES.map(([title, subtitle], index) => (
            <div key={title} className="flex items-center gap-2">
              <div
                className={`min-w-[110px] rounded-xl border px-2.5 py-2 ${
                  activeStage === index ? "border-[var(--brand-header)]" : "border-[var(--border)]"
                }`}
              >
                <p className={`text-sm font-extrabold ${activeStage === index ? "text-[var(--brand-header)]" : ""}`}>
                  {title}
                </p>
                <p className="text-[11px] text-[var(--text-muted)]">{subtitle}</p>
              </div>
              {index < STAGES.length - 1 ? <span className="hidden text-[#9ca3af] lg:inline">⋯⋯⋯</span> : null}
            </div>
          ))}
        </div>
      </section>

      <section className="card space-y-2 opacity-60" title="Planned for a later release.">
        <h2 className="text-sm font-bold">BBX Images from Previous Cases</h2>
        <p>Index bounding box images from previous cases to enable cross-case correlation.</p>
        <p>Only cases you select will be indexed. Not all cases need to be indexed.</p>
        <button type="button" className="btn btn-primary" disabled>
          Index BBX Images...
        </button>
        <p className="text-xs text-[var(--text-muted)]">No BBX images indexed yet.</p>
      </section>

      <section className="card space-y-3">
        <h2 className="text-sm font-bold">Verify Index</h2>
        <p>Browse sample images from each indexed dataset to confirm indexing succeeded.</p>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => {
            setPreviewCorpus(String(selected[0] || coverageRows[0]?.id || ""));
            setPreviewOpen(true);
          }}
        >
          Preview Indexed Images...
        </button>
        <div>
          <p className="text-sm font-bold">Recall check</p>
          {(recall.data?.results ?? []).map((row) => (
            <p
              key={row.encoder_id}
              className={row.measured_recall ? "text-[var(--approved-dark)]" : "text-[var(--warning)]"}
            >
              {row.encoder_name}: {formatRecall(row)}
            </p>
          ))}
          {!recall.data?.results.length ? (
            <p className="text-[var(--warning)]">
              Not measured. Approximate search has not been validated for this corpus.
            </p>
          ) : null}
        </div>
        <ul className="space-y-1 text-sm font-bold">
          {coverageRows.map((row) => (
            <li key={row.id}>
              {row.name}: {(row.indexed_count ?? 0).toLocaleString()} / {(row.evidence_count ?? 0).toLocaleString()} indexed
            </li>
          ))}
        </ul>
      </section>

      <div className="card space-y-3" ref={progressRef}>
        <h2 className="text-sm font-bold">Progress</h2>
        <ul className="space-y-1 text-base font-extrabold">
          {coverageRows.map((row) => (
            <li key={`progress-${row.id}`}>
              {row.name}: {(row.indexed_count ?? 0).toLocaleString()} / {(row.evidence_count ?? 0).toLocaleString()} indexed
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn btn-primary"
            disabled={!selected.length || index.isPending}
            onClick={() => index.mutate()}
          >
            Start indexing
          </button>
          <button type="button" className="btn" disabled={!active.length} onClick={() => pause.mutate()}>
            {pausing ? "Pausing…" : "Pause"}
          </button>
          <button
            type="button"
            className="btn"
            disabled={!!active.length}
            onClick={() => index.mutate()}
          >
            Resume
          </button>
        </div>
        <p className="text-sm">
          {progressCaption}
        </p>
        <div className="h-3 overflow-hidden rounded-[10px] border border-[var(--border)]">
          <div
            className="h-full bg-[var(--brand-header)]"
            style={{ width: `${focus ? taskBarPercent(focus) : 0}%` }}
          />
        </div>
        <p className="text-[13px] font-bold">Log (tail)</p>
        <button type="button" className="text-xs md:hidden" onClick={() => setShowLog((open) => !open)}>
          {showLog ? "Hide log" : "Show log"}
        </button>
        {showLog ? (
          <pre
            ref={logRef}
            className="min-h-[140px] overflow-auto rounded-[10px] bg-[var(--surface-muted)] p-2 font-mono text-xs text-[var(--text-secondary)]"
          >
            {logs.join("\n") || "No indexing runs yet."}
          </pre>
        ) : null}
        {message ? <p>{message}</p> : null}
        <ul className="space-y-1 text-sm">
          {CHECKLIST.map((item, index) => (
            <li key={item} className="flex items-center gap-2">
              <span>{activeStage >= index || done ? "☑" : "☐"}</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
        {done ? (
          <p>
            <Link className="font-extrabold text-[var(--brand-header)]" to="/search">
              Search this corpus →
            </Link>
          </p>
        ) : null}
      </div>

      {previewOpen ? (
        <IndexPreview
          corpora={corpora.data?.results ?? []}
          corpusId={previewCorpus}
          onCorpus={setPreviewCorpus}
          onClose={() => setPreviewOpen(false)}
        />
      ) : null}
    </div>
  );
}

function mergeCoverage(primary?: CorpusRow[], fallback?: CorpusRow[]): CorpusRow[] {
  const byId = new Map<number, CorpusRow>();
  for (const row of [...(fallback ?? []), ...(primary ?? [])]) {
    const prev = byId.get(row.id);
    if (!prev) {
      byId.set(row.id, row);
      continue;
    }
    byId.set(row.id, {
      ...prev,
      ...row,
      evidence_count: Math.max(prev.evidence_count ?? 0, row.evidence_count ?? 0),
      indexed_count: Math.max(prev.indexed_count ?? 0, row.indexed_count ?? 0),
    });
  }
  return [...byId.values()];
}

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = String(Math.floor(total / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

function IndexPreview({
  corpora,
  corpusId,
  onCorpus,
  onClose,
}: {
  corpora: CorpusRow[];
  corpusId: string;
  onCorpus: (id: string) => void;
  onClose: () => void;
}) {
  const coverage = useQuery({
    queryKey: ["corpora", "coverage"],
    queryFn: () => api<{ results: CorpusRow[] }>("/api/v1/corpora/coverage/"),
  });
  const evidence = useQuery({
    queryKey: ["evidence", "preview", corpusId],
    queryFn: () =>
      api<{ results: EvidenceRow[] }>(`/api/v1/evidence/?corpus=${corpusId}&state=indexed&page_size=24`),
    enabled: Boolean(corpusId),
  });
  const chosen = (coverage.data?.results ?? corpora).find((row) => String(row.id) === corpusId);
  const indexed = chosen?.indexed_count ?? evidence.data?.results.length ?? 0;

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div className="card flex max-h-[90vh] w-full max-w-[860px] flex-col" style={{ minHeight: 560 }}>
        <h2 className="text-sm font-bold">Index Preview</h2>
        <label className="mt-2 block max-w-xs">
          Dataset:
          <select className="field mt-1" value={corpusId} onChange={(e) => onCorpus(e.target.value)}>
            {corpora.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
        <p className={`mt-3 rounded-[10px] px-3 py-2 ${indexed ? "bg-[#ecfdf5] text-[var(--approved-dark)]" : "bg-[#fef2f2] text-[var(--danger-dark)]"}`}>
          {indexed
            ? `${indexed.toLocaleString()} images indexed`
            : "Not indexed — 0 images found in the index for this folder."}
        </p>
        <div className="mt-3 grid flex-1 grid-cols-2 gap-2 overflow-auto md:grid-cols-4">
          {(evidence.data?.results ?? []).map((row) => (
            <figure key={row.public_id} className="rounded-[10px] border border-[var(--border)] p-2">
              <SignedThumb evidenceId={row.public_id} alt={row.original_filename} className="h-28 w-full object-cover" />
              <figcaption className="mt-1 truncate text-xs">{row.original_filename || row.public_id}</figcaption>
            </figure>
          ))}
          {!evidence.data?.results.length ? <p className="col-span-full">No indexed images to show.</p> : null}
        </div>
        <div className="mt-3 flex justify-end">
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
