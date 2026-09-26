import { KeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  ApiError,
  CaseRow,
  CorpusRow,
  QueryRow,
  ResultRow,
  RunRow,
  api,
  rowsOf,
} from "../api";
import { AppIcon, IconAction } from "../assets/AppIcon";
import { setHeaderContext } from "../shellContext";
import { SignedThumb } from "./SignedThumb";
import { StarRating } from "./StarRating";

type Scope = "Both" | "Ecom" | "NDFsim" | "PreviousCases" | "All";
type Tab = "previous" | "current" | "text";
type LocalQuery = {
  id: string;
  publicId?: string;
  kind: "image" | "text";
  checked: boolean;
  file?: File;
  preview?: string;
  filename?: string;
  evidenceId?: string;
  text?: string;
  status?: string;
  resultCount?: number;
};

const SCOPES: Scope[] = ["Both", "Ecom", "NDFsim", "PreviousCases", "All"];
const SUGGESTIONS = [
  "nike black sneakers",
  "white trainer sole",
  "adidas three stripe",
  "hiking boot tread",
  "red high heel",
];

function confidenceScore(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(3);
}

function uid(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function pickHistoryRun(rows: RunRow[], preferred = ""): RunRow | undefined {
  return (
    rows.find((row) => row.public_id === preferred) ||
    rows.find((row) => row.saved && (row.query_count || 0) > 0) ||
    rows.find((row) => row.saved)
  );
}

function queryToLocal(row: QueryRow): LocalQuery {
  return {
    id: row.public_id,
    publicId: row.public_id,
    kind: row.query_type === "text" ? "text" : "image",
    checked: true,
    filename: row.probe_filename || undefined,
    evidenceId: row.probe_evidence_id || undefined,
    text: row.query_text || undefined,
    status: row.status,
    resultCount: row.result_count,
  };
}

function weightLabel(value: number): string {
  return value.toFixed(2);
}

function revokePreviews(rows: LocalQuery[]) {
  for (const row of rows) {
    if (row.preview) URL.revokeObjectURL(row.preview);
  }
}

export function SearchPage() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const cases = useQuery({
    queryKey: ["cases"],
    queryFn: () => api<{ results: CaseRow[] }>("/api/v1/cases/?page_size=100"),
  });
  const corpora = useQuery({
    queryKey: ["corpora"],
    queryFn: () => api<{ results: CorpusRow[] }>("/api/v1/corpora/?page_size=100"),
  });

  const [caseId, setCaseId] = useState(params.get("case") || "");
  const [runId, setRunId] = useState(params.get("run") || "");
  const [tab, setTab] = useState<Tab>(params.get("case") ? "previous" : "current");
  const [scope, setScope] = useState<Scope>("All");
  const [models, setModels] = useState<string[]>(["CLIP", "DINO"]);
  const [k, setK] = useState("10");
  const [lambda, setLambda] = useState(0.5);
  const [alpha, setAlpha] = useState(0.85);
  const [imageQueries, setImageQueries] = useState<LocalQuery[]>([]);
  const [textQueries, setTextQueries] = useState<LocalQuery[]>([]);
  const [draft, setDraft] = useState("");
  const [selectedQuery, setSelectedQuery] = useState<string>("");
  const [layout, setLayout] = useState<"list" | "grid">("grid");
  const [selectedResult, setSelectedResult] = useState("");
  const [ratings, setRatings] = useState<Record<string, number>>({});
  const [fullImage, setFullImage] = useState<{ src?: string; evidenceId?: string } | null>(null);
  const [noteTarget, setNoteTarget] = useState("");
  const [webQuery, setWebQuery] = useState("");
  const [alert, setAlert] = useState("");
  const [toast, setToast] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [sourceOpen, setSourceOpen] = useState(false);
  const [storageOpen, setStorageOpen] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [webOpen, setWebOpen] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteBody, setNoteBody] = useState("");
  const [fullOpen, setFullOpen] = useState(false);
  const [approvalMode, setApprovalMode] = useState(false);
  const [saved, setSaved] = useState(Boolean(params.get("run")));
  const [busy, setBusy] = useState(false);
  const [historyRun, setHistoryRun] = useState(params.get("closed") === "1" ? "" : params.get("run") || "");
  const [searchClosed, setSearchClosed] = useState(params.get("closed") === "1");
  const skipAutoLoad = useRef(params.get("closed") === "1");
  const waitingForUrlClear = useRef(params.get("closed") === "1");

  const activeCase = (cases.data?.results ?? []).find((row) => row.public_id === caseId);
  const isText = tab === "text";
  const urlCase = params.get("case") || "";
  const urlRun = params.get("run") || "";
  const urlQuery = params.get("query") || "";
  const urlClosed = params.get("closed") === "1";

  const runs = useQuery({
    queryKey: ["runs", caseId],
    queryFn: () => api<RunRow[]>(`/api/v1/cases/${caseId}/runs/`),
    enabled: Boolean(caseId),
  });
  const savedQueries = useQuery({
    queryKey: ["run-queries", historyRun],
    queryFn: () => api<QueryRow[]>(`/api/v1/runs/${historyRun}/queries/`),
    enabled: Boolean(historyRun),
  });

  useEffect(() => {
    if (waitingForUrlClear.current && !urlRun) waitingForUrlClear.current = false;
  }, [urlRun]);

  useEffect(() => {
    if (urlClosed) {
      skipAutoLoad.current = true;
      setSearchClosed(true);
      setRunId("");
      setHistoryRun("");
      setSaved(false);
      setSelectedQuery("");
      setImageQueries([]);
      setTextQueries([]);
      setTab("current");
      return;
    }
    if (urlCase && urlCase !== caseId) {
      skipAutoLoad.current = false;
      waitingForUrlClear.current = false;
      setSearchClosed(false);
      setCaseId(urlCase);
      setRunId(urlRun);
      setHistoryRun(urlRun);
      setSaved(Boolean(urlRun));
      setTab(urlRun ? "previous" : "current");
      setSelectedQuery(urlQuery);
      setImageQueries([]);
      setTextQueries([]);
      return;
    }
    if (skipAutoLoad.current) {
      if (waitingForUrlClear.current) return;
      if (urlRun) {
        skipAutoLoad.current = false;
        setSearchClosed(false);
        setHistoryRun(urlRun);
        setRunId(urlRun);
        setSaved(true);
        setTab("previous");
        if (urlQuery) setSelectedQuery(urlQuery);
      }
    }
  }, [urlCase, urlRun, urlQuery, urlClosed, caseId]);

  useEffect(() => {
    if (skipAutoLoad.current || searchClosed || urlClosed || !urlRun) return;
    const rows = rowsOf(runs.data);
    if (!caseId || !rows.length) return;
    const chosen = pickHistoryRun(rows, urlRun);
    if (!chosen) return;
    if (chosen.public_id === historyRun && chosen.public_id === runId) return;
    setHistoryRun(chosen.public_id);
    setRunId(chosen.public_id);
    setSaved(Boolean(chosen.saved));
    setTab("previous");
    setToast(`Loaded ${chosen.saved ? "saved " : ""}run ${chosen.label}`);
  }, [runs.data, caseId, urlRun, urlClosed, searchClosed, historyRun, runId]);

  useEffect(() => {
    if (activeCase) {
      const run = runId ? ` · Run: ${runId.slice(0, 8)}` : "";
      setHeaderContext(`Case: ${activeCase.name}${run}`);
    } else {
      setHeaderContext("");
    }
    return () => setHeaderContext("");
  }, [activeCase, runId]);

  useEffect(() => {
    if (skipAutoLoad.current || searchClosed) return;
    const rows = rowsOf(savedQueries.data);
    if (!rows.length) return;
    for (const row of rows) {
      const local = queryToLocal(row);
      if (row.query_type === "text") setTextQueries((current) => [...current.filter((item) => item.publicId !== row.public_id), local]);
      else setImageQueries((current) => [...current.filter((item) => item.publicId !== row.public_id), local]);
    }
    setSelectedQuery((current) => {
      if (urlQuery && rows.some((row) => row.public_id === urlQuery)) return urlQuery;
      if (current && rows.some((row) => row.public_id === current)) return current;
      return rows[0].public_id;
    });
  }, [savedQueries.data, urlQuery]);

  useEffect(() => {
    if (skipAutoLoad.current || searchClosed) return;
    if (!caseId || !historyRun || !saved) return;
    if (params.get("case") === caseId && params.get("run") === historyRun) return;
    writeLocation(caseId, historyRun, selectedQuery || urlQuery);
  }, [caseId, historyRun, saved, selectedQuery, urlQuery, searchClosed]);

  const liveQuery = imageQueries.concat(textQueries).find((row) => row.id === selectedQuery);
  const results = useQuery({
    queryKey: ["results", liveQuery?.publicId, liveQuery?.resultCount],
    queryFn: async () => {
      try {
        return await api<ResultRow[]>(`/api/v1/queries/${liveQuery?.publicId}/results/`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) return [] as ResultRow[];
        throw error;
      }
    },
    enabled: Boolean(liveQuery?.publicId && liveQuery.status === "complete"),
    staleTime: 0,
  });
  const liveStatus = useQuery({
    queryKey: ["query", liveQuery?.publicId],
    queryFn: () => api<QueryRow>(`/api/v1/queries/${liveQuery?.publicId}/`),
    enabled: Boolean(liveQuery?.publicId && liveQuery.status !== "complete"),
    refetchInterval: 2500,
  });

  useEffect(() => {
    if (!liveStatus.data || !liveQuery) return;
    const apply = (row: LocalQuery) =>
      row.id === liveQuery.id
        ? { ...row, status: liveStatus.data.status, resultCount: liveStatus.data.result_count }
        : row;
    setImageQueries((rows) => rows.map(apply));
    setTextQueries((rows) => rows.map(apply));
  }, [liveStatus.data, liveQuery?.id]);

  const corpusIds = useMemo(() => {
    const list = rowsOf(corpora.data);
    const byCode = (code: string) =>
      list.filter((row) => row.code.toLowerCase() === code.toLowerCase()).map((row) => row.id);
    if (scope === "All") return list.map((row) => row.id);
    if (scope === "Both") return [...byCode("Ecom"), ...byCode("NDFsim")];
    return byCode(scope);
  }, [corpora.data, scope]);

  function writeLocation(nextCase: string, nextRun = "", nextQuery = "", closed = false) {
    const next = new URLSearchParams();
    if (nextCase) next.set("case", nextCase);
    if (nextRun) next.set("run", nextRun);
    if (nextQuery) next.set("query", nextQuery);
    if (closed) next.set("closed", "1");
    setParams(next, { replace: true });
  }

  function setCase(next: string) {
    skipAutoLoad.current = false;
    waitingForUrlClear.current = false;
    setSearchClosed(false);
    setCaseId(next);
    setRunId("");
    setHistoryRun("");
    setSaved(false);
    setSelectedQuery("");
    writeLocation(next);
  }

  const createCase = useMutation({
    mutationFn: () => api<CaseRow>("/api/v1/cases/", { method: "POST", body: JSON.stringify({ name: newName }) }),
    onSuccess: (row) => {
      setCreateOpen(false);
      setNewName("");
      client.invalidateQueries({ queryKey: ["cases"] });
      setCase(row.public_id);
    },
  });

  function addImages(files: FileList | null) {
    if (!caseId) {
      setAlert("Please create a case first before selecting images.");
      return;
    }
    if (isText) return;
    const incoming = Array.from(files || []);
    if (!incoming.length) {
      setAlert("Please select images to search… Use 'Select Images'");
      return;
    }
    const existing = new Set(imageQueries.map((row) => row.filename));
    const fresh = incoming.filter((file) => !existing.has(file.name));
    if (!fresh.length) {
      setAlert("All selected images are already in the query list.");
      return;
    }
    setAlert("");
    setImageQueries((rows) => [
      ...rows,
      ...fresh.map((file) => ({
        id: uid(),
        kind: "image" as const,
        checked: true,
        file,
        preview: URL.createObjectURL(file),
        filename: file.name,
      })),
    ]);
    setTab("current");
  }

  function addText(value = draft) {
    const text = value.trim();
    if (!text) {
      setAlert("Please add a text query… Example: nike black sneakers");
      return;
    }
    setTextQueries((rows) => [...rows, { id: uid(), kind: "text", checked: true, text }]);
    setDraft("");
    setAlert("");
    setTab("text");
  }

  function onDraftKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && event.ctrlKey) {
      event.preventDefault();
      addText();
    }
  }

  async function runSearch() {
    const pool = isText ? textQueries : imageQueries;
    const selected = pool.filter((row) => row.checked);
    if (!caseId) {
      setAlert("Please create a case first before selecting images.");
      return;
    }
    if (!selected.length) {
      setAlert(isText ? "Please add a text query… Example: nike black sneakers" : "Please select images to search… Use 'Select Images'");
      return;
    }
    skipAutoLoad.current = false;
    waitingForUrlClear.current = false;
    setSearchClosed(false);
    setBusy(true);
    setAlert("");
    try {
      const metadataWeight = (1 - alpha).toFixed(3);
      const useClip = isText || models.includes("CLIP");
      const useDinov2 = !isText && models.includes("DINO");
      const run = await api<RunRow>(`/api/v1/cases/${caseId}/runs/`, {
        method: "POST",
        body: JSON.stringify({
          top_k: Math.min(100, Math.max(1, Number(k) || 10)),
          model_weight: lambda.toFixed(3),
          metadata_weight: metadataWeight,
          corpus_ids: corpusIds,
          use_clip: useClip,
          use_dinov2: useDinov2,
        }),
      });
      setRunId(run.public_id);
      setSaved(false);
      writeLocation(caseId, run.public_id);
      const next: LocalQuery[] = [];
      for (const item of selected) {
        if (item.kind === "text") {
          const query = await api<QueryRow>(`/api/v1/runs/${run.public_id}/queries/`, {
            method: "POST",
            body: JSON.stringify({ query_type: "text", query_text: item.text }),
          });
          next.push({ ...item, publicId: query.public_id, status: query.status, resultCount: query.result_count });
        } else if (item.file) {
          const body = new FormData();
          body.append("file", item.file);
          body.append("query_type", "image");
          if (corpusIds[0]) body.append("corpus", String(corpusIds[0]));
          const query = await api<QueryRow>(`/api/v1/runs/${run.public_id}/queries/`, { method: "POST", body });
          next.push({ ...item, publicId: query.public_id, status: query.status, resultCount: query.result_count });
        }
      }
      if (isText) setTextQueries((rows) => rows.map((row) => next.find((item) => item.id === row.id) || row));
      else setImageQueries((rows) => rows.map((row) => next.find((item) => item.id === row.id) || row));
      if (next[0]) setSelectedQuery(next[0].id);
      setTab(isText ? "text" : "current");
      setToast(`Queued ${next.length} ${next.length === 1 ? "query" : "queries"}. Waiting for results…`);
      client.invalidateQueries({ queryKey: ["runs", caseId] });
      client.invalidateQueries({ queryKey: ["tasks"] });
    } catch (error) {
      setAlert(error instanceof ApiError ? error.message : "Search failed");
    } finally {
      setBusy(false);
    }
  }

  function clearSearch() {
    skipAutoLoad.current = true;
    waitingForUrlClear.current = true;
    setSearchClosed(true);
    revokePreviews(imageQueries);
    revokePreviews(textQueries);
    setImageQueries([]);
    setTextQueries([]);
    setSelectedQuery("");
    setRunId("");
    setHistoryRun("");
    setSaved(false);
    setAlert("");
    setToast("");
    setDraft("");
    setApprovalMode(false);
    setNoteOpen(false);
    setFullOpen(false);
    setFullImage(null);
    setWebOpen(false);
    setNoteTarget("");
    setWebQuery("");
    setSelectedResult("");
    setRatings({});
    setTab("current");
    client.removeQueries({ queryKey: ["results"] });
    client.removeQueries({ queryKey: ["query"] });
    writeLocation(caseId, "", "", true);
    setClearOpen(false);
  }

  const historyRows = searchClosed ? [] : rowsOf(runs.data);
  const checkedCount = (isText ? textQueries : imageQueries).filter((row) => row.checked).length;
  const preview = (isText ? textQueries : imageQueries).find((row) => row.id === selectedQuery);
  const resultRows = results.data ?? [];
  const webSearch = webQuery || preview?.filename || preview?.text || "";
  const failed = preview?.status === "failed";
  const pending = Boolean(preview?.publicId && preview.status && preview.status !== "complete" && !failed);
  const ranking = `Default ranking: Scope=${scope} | Models=${models.join("+")} | K=${k} | λ=${weightLabel(lambda)} | α=${weightLabel(alpha)}`;

  useEffect(() => {
    const rows = results.data ?? [];
    if (!rows.length) {
      setSelectedResult("");
      return;
    }
    if (!rows.some((row) => row.public_id === selectedResult)) {
      setSelectedResult(rows[0].public_id);
    }
  }, [results.data, selectedResult]);

  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setApprovalMode(false);
        setFullOpen(false);
        setFullImage(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="space-y-2 p-2">
      <section className="card space-y-3" translate="no">
        <h2 className="text-sm font-bold">Parameters</h2>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-bold">Scope:</span>
          {SCOPES.map((item) => (
            <button
              key={item}
              type="button"
              translate="no"
              className={`chip ${scope === item ? "chip-on" : ""}`}
              onClick={() => setScope(item)}
            >
              {item}
            </button>
          ))}
          <span className="ml-3 font-bold">Models:</span>
          {["CLIP", "DINO"].map((item) =>
            isText && item === "DINO" ? null : (
              <button
                key={item}
                type="button"
                translate="no"
                className={`chip ${models.includes(item) || (isText && item === "CLIP") ? "chip-on" : ""}`}
                disabled={isText && item === "CLIP"}
                onClick={() =>
                  setModels((current) => {
                    const next = current.includes(item) ? current.filter((name) => name !== item) : [...current, item];
                    return next.length ? next : current;
                  })
                }
              >
                {item}
              </button>
            ),
          )}
          <label className="ml-3 flex items-center gap-2 font-bold">
            K:
            <input
              className="field w-[90px]"
              type="number"
              min={1}
              max={100}
              value={k}
              onChange={(e) => setK(e.target.value)}
            />
          </label>
        </div>
        {isText ? null : (
          <div className="grid gap-3 md:grid-cols-2">
            <label>
              λ (DINO weight):
              <div className="flex items-center gap-3">
                <input
                  className="weight-slider"
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={lambda}
                  onChange={(e) => setLambda(Number(e.target.value))}
                />
                <span className="min-w-[44px] text-right">{weightLabel(lambda)}</span>
              </div>
            </label>
            <label>
              α (visual weight):
              <div className="flex items-center gap-3">
                <input
                  className="weight-slider"
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={alpha}
                  onChange={(e) => setAlpha(Number(e.target.value))}
                />
                <span className="min-w-[44px] text-right">{weightLabel(alpha)}</span>
              </div>
            </label>
          </div>
        )}
        <p className="text-xs text-[var(--text-muted)]" translate="no">
          {ranking}
          {corpusIds.length ? ` | corpora=${corpusIds.length}` : " | corpora=none (select a scope)"}
        </p>
      </section>

      <section className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn btn-primary"
          title={
            activeCase
              ? `Current case: ${activeCase.name}\nClick to create a new case.`
              : "Create a new case to organize your searches."
          }
          onClick={() => setCreateOpen(true)}
        >
          {activeCase ? "New Case" : "Create Case"}
        </button>
        <button type="button" className="btn" disabled={!caseId} onClick={() => setSourceOpen(true)}>
          Evidence Source
        </button>
        <button type="button" className="btn" disabled={!caseId} onClick={() => setStorageOpen(true)}>
          Case Storage
        </button>
        <label className={`btn ${!caseId || isText ? "pointer-events-none opacity-40" : ""}`} title={caseId ? "Select images from working directory" : "Create a case first to select images"}>
          Select Images…
          <input className="hidden" type="file" accept="image/*" multiple disabled={!caseId || isText} onChange={(e) => addImages(e.target.files)} />
        </label>
        <span className="flex-1" />
        <button type="button" className="btn btn-primary" disabled={!checkedCount || busy} onClick={runSearch}>
          Run
        </button>
        <button type="button" className="btn" disabled title="Planned for a later release.">
          Prepare Evidence
        </button>
        <button type="button" className="btn" disabled title="Planned for a later release.">
          Crop BBX ▾
        </button>
        <button
          type="button"
          className="btn"
          disabled={!runId || saved}
          title={saved ? "This run has already been saved." : "Save this run so Cases, Review and later sessions can reload it."}
          onClick={() => {
            if (!runId) return;
            api<RunRow>(`/api/v1/runs/${runId}/save/`, { method: "POST" })
              .then((row) => {
                setSearchClosed(false);
                setSaved(true);
                setHistoryRun(row.public_id);
                setTab("previous");
                writeLocation(caseId, row.public_id, liveQuery?.publicId || params.get("query") || "");
                client.invalidateQueries({ queryKey: ["runs", caseId] });
                setToast("Run saved to history. It will reload from Cases, Review, and this URL.");
              })
              .catch((error) => setAlert(error instanceof ApiError ? error.message : "Could not save that run."));
          }}
        >
          Save Run to History
        </button>
        <button type="button" className="btn" onClick={() => setClearOpen(true)}>
          Close Search
        </button>
      </section>

      {alert ? <p className="rounded-[10px] bg-[#fef2f2] px-3 py-2 text-[var(--danger-dark)]">{alert}</p> : null}
      {toast ? <p className="text-xs text-[var(--text-muted)]">{toast}</p> : null}

      <div className="grid gap-2 lg:grid-cols-[minmax(320px,35%)_1fr]">
        <section className="card min-h-[420px] space-y-3">
          <div className="flex flex-wrap gap-1 rounded-xl bg-[var(--surface-muted)] p-1">
            {(
              [
                ["previous", "Previous Runs"],
                ["current", "Current Search"],
                ["text", "Text Search"],
              ] as const
            ).map(([key, label]) => (
              <button key={key} type="button" className={`chip ${tab === key ? "chip-on" : ""}`} onClick={() => setTab(key)}>
                {label}
              </button>
            ))}
          </div>

          {tab === "previous" ? (
            <div className="space-y-2">
              <p className="font-bold">Run History ({historyRows.length})</p>
              {caseId ? (
                <p className="text-[#2563eb]">
                  Total: {historyRows.reduce((sum, row) => sum + (row.query_count || 0), 0)} | Saved: {historyRows.filter((row) => row.saved).length} | Not saved: {historyRows.filter((row) => !row.saved).length}
                </p>
              ) : null}
              <ul className="space-y-2">
                {historyRows.map((row) => (
                  <li
                    key={row.public_id}
                    className={`flex cursor-pointer items-center gap-3 rounded-[10px] p-2 ${historyRun === row.public_id ? "bg-[var(--select-blue)]" : ""}`}
                    onClick={() => {
                      skipAutoLoad.current = false;
                      waitingForUrlClear.current = false;
                      setSearchClosed(false);
                      setHistoryRun(row.public_id);
                      setRunId(row.public_id);
                      setSaved(true);
                      writeLocation(caseId, row.public_id);
                    }}
                    title={`${row.label}\n${row.query_count || 0} queries\n${JSON.stringify({ K: row.top_k, λ: row.model_weight, α: (1 - Number(row.metadata_weight)).toFixed(2) })}`}
                  >
                    <span className="flex h-11 w-11 items-center justify-center rounded-full bg-[#dcfce7] font-extrabold">R</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-extrabold">{row.label}{row.saved ? " · saved" : ""}</p>
                      <p className="text-xs text-[var(--text-muted)]">
                        {row.query_count ? `${row.query_count} ${row.query_count === 1 ? "query" : "queries"}` : "No queries"}
                      </p>
                    </div>
                    <StarRating value={0} onChange={() => undefined} readOnly compact />
                    <IconAction
                      name="displayNotes"
                      label="Run Note"
                      size={30}
                      onClick={(event) => {
                        event.stopPropagation();
                        if (!saved && !runId) setAlert("Notes can only be added to saved queries. Please save the run first.");
                        else setNoteOpen(true);
                      }}
                    />
                    <IconAction
                      name="list"
                      label="List view"
                      size={30}
                      active={layout === "list"}
                      onClick={(event) => {
                        event.stopPropagation();
                        setLayout("list");
                      }}
                    />
                    <IconAction
                      name="grid"
                      label="Grid view (3 per row)"
                      size={30}
                      active={layout === "grid"}
                      onClick={(event) => {
                        event.stopPropagation();
                        setLayout("grid");
                      }}
                    />
                  </li>
                ))}
              </ul>
              {historyRun && !searchClosed ? (
                <div className="max-h-[280px] overflow-auto">
                  <p className="mb-1 font-bold">Saved Run Queries</p>
                  <ul className="space-y-1">
                    {rowsOf(savedQueries.data).map((row) => (
                      <li key={row.public_id}>
                        <button
                          type="button"
                          className={`flex w-full items-center gap-2 rounded-[10px] px-2 py-1 text-left ${selectedQuery === row.public_id ? "bg-[var(--select-blue)]" : "hover:bg-[var(--surface-muted)]"}`}
                          onClick={() => {
                            const local = queryToLocal(row);
                            if (row.query_type === "text") setTextQueries((rows) => [...rows.filter((item) => item.publicId !== row.public_id), local]);
                            else setImageQueries((rows) => [...rows.filter((item) => item.publicId !== row.public_id), local]);
                            setSelectedQuery(row.public_id);
                            writeLocation(caseId, historyRun || runId, row.public_id);
                          }}
                        >
                          {row.query_type === "image" ? (
                            <SignedThumb
                              evidenceId={row.probe_evidence_id || undefined}
                              alt={row.probe_filename || `Query ${row.sequence}`}
                              className="h-11 w-11 rounded-[10px] object-cover"
                            />
                          ) : (
                            <span className="flex h-11 w-11 items-center justify-center rounded-[10px] bg-[var(--surface-muted)]">Aa</span>
                          )}
                          <span className="min-w-0 flex-1 truncate">
                            {row.query_type === "text" ? row.query_text : row.probe_filename || `Query ${row.sequence}`}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}

          {tab === "current" ? (
            <QueryList
              label="Active Queries"
              rows={imageQueries}
              selected={selectedQuery}
              onSelect={setSelectedQuery}
              onChange={setImageQueries}
            />
          ) : null}

          {tab === "text" ? (
            <div className="space-y-2">
              <p className="font-bold">Text Queries</p>
              <div className="flex gap-2">
                <textarea
                  className="field h-24 flex-1"
                  placeholder="Type a query (Ctrl+Enter to add)…"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={onDraftKey}
                />
                <div className="flex w-[120px] flex-col gap-1">
                  <button type="button" className="btn" onClick={() => addText()}>
                    Add
                  </button>
                  <button type="button" className="btn" onClick={() => setClearOpen(true)}>
                    Clear
                  </button>
                  <button type="button" className="btn" title="Suggested text queries" onClick={() => setSuggestOpen(true)}>
                    Suggestions
                  </button>
                </div>
              </div>
              <QueryList
                label=""
                rows={textQueries}
                selected={selectedQuery}
                onSelect={setSelectedQuery}
                onChange={setTextQueries}
                text
              />
            </div>
          ) : null}
        </section>

        <section className="space-y-2">
          <div className="card flex flex-col items-center" translate="no">
            <div className="relative flex h-[200px] w-[270px] items-center justify-center border-2 border-[#5dade2] bg-[#f3f4f6]">
              {preview?.kind === "image" && preview.preview ? (
                <img src={preview.preview} alt="" className="h-full w-full object-contain" />
              ) : preview?.kind === "text" ? (
                <span className="text-4xl">Aa</span>
              ) : preview?.kind === "image" && preview.evidenceId ? (
                <SignedThumb evidenceId={preview.evidenceId} variant="original" className="h-full w-full object-contain" />
              ) : (
                <span className="text-[var(--text-muted)]">—</span>
              )}
              {preview?.kind === "text" ? (
                <span className="absolute right-1 top-1 rounded bg-[var(--brand-header)] px-2 text-xs text-white">TEXT</span>
              ) : null}
              {resultRows.some((row) => row.approval_state === "approved" || row.approval_state === "countersigned") ? (
                <span className="absolute right-1 top-1 rounded border-2 border-white bg-[var(--approved,#10b981)] px-2 text-[11px] font-bold text-white">
                  ✓ APPROVED
                </span>
              ) : null}
            </div>
            <div className="mt-2 w-[270px] text-sm">
              {preview?.kind === "text" ? (
                <p>
                  <span className="font-bold">Text query:</span> {preview.text || "—"}
                </p>
              ) : (
                <>
                  <p>
                    <span className="font-bold">File name:</span> {preview?.filename || "—"}
                  </p>
                  <p className="truncate text-[var(--text-muted)]">
                    <span className="font-bold text-[var(--text)]">Path:</span> {preview?.filename || "—"}
                  </p>
                </>
              )}
            </div>
            <div className="mt-2 flex flex-wrap justify-center gap-1.5">
              <IconAction
                name="validation"
                label={approvalMode ? "Click again to finish approval" : "Validation"}
                size={44}
                active={approvalMode}
                onClick={() => setApprovalMode((on) => !on)}
              />
              <IconAction name="linkFolder" label="Link Folder" size={44} onClick={() => setStorageOpen(true)} />
              <IconAction
                name="linkWeb"
                label="Link Web1"
                size={44}
                onClick={() => {
                  setWebQuery(preview?.filename || preview?.text || "");
                  setWebOpen(true);
                }}
              />
              <IconAction name="aiBrain" label="Ai-Brain" size={44} disabled />
              <IconAction
                name="writeNote"
                label="Write Note"
                size={44}
                onClick={() => {
                  if (!saved && !runId) setAlert("Notes can only be added to saved queries. Please save the run first.");
                  else {
                    setNoteTarget(resultRows[0]?.public_id || "");
                    setNoteOpen(true);
                  }
                }}
              />
              <IconAction
                name="fullscreen"
                label="Fullscreen"
                size={44}
                onClick={() => {
                  if (preview?.preview) setFullImage({ src: preview.preview });
                  else if (preview?.evidenceId) setFullImage({ evidenceId: preview.evidenceId });
                  setFullOpen(true);
                }}
              />
            </div>
          </div>

          <div className={`grid gap-2.5 p-2.5 ${layout === "grid" ? "md:grid-cols-3" : "grid-cols-1"}`} translate="no">
            {failed ? (
              <p className="col-span-full text-[var(--danger-dark)]">
                Search failed. Click Run again.
              </p>
            ) : pending
              ? Array.from({ length: Number(k) || 10 }).map((_, index) => (
                  <article key={index} className="rounded-[12px] border border-[var(--border)] p-3">
                    <p className="text-[var(--text-muted)]">
                      {preview?.status === "pending" || preview?.status === "queued"
                        ? `Queued · position ${index + 1}`
                        : preview?.status === "encoding"
                          ? "Encoding…"
                          : "Searching…"}
                    </p>
                  </article>
                ))
              : resultRows.map((row) => (
                  <ResultCard
                    key={row.public_id}
                    row={row}
                    layout={layout}
                    selected={selectedResult === row.public_id}
                    rating={ratings[row.public_id] || 0}
                    approvalMode={approvalMode}
                    onSelect={() => setSelectedResult(row.public_id)}
                    onRate={(value) => {
                      setRatings((current) => ({ ...current, [row.public_id]: value }));
                      api("/api/v1/review/ratings/", {
                        method: "POST",
                        body: JSON.stringify({ result_public_id: row.public_id, value, scope: "result" }),
                      }).catch((error) =>
                        setAlert(error instanceof ApiError ? error.message : "Could not save that rating."),
                      );
                    }}
                    onApprove={() => {
                      if (!approvalMode) return;
                      api<{ state: string }>("/api/v1/review/approvals/", {
                        method: "POST",
                        body: JSON.stringify({ result_public_id: row.public_id }),
                      })
                        .then((approval) => {
                          setAlert("");
                          setToast(
                            approval.state === "approved" || approval.state === "countersigned"
                              ? "Result approved"
                              : "Approval withdrawn",
                          );
                          client.invalidateQueries({ queryKey: ["results"] });
                        })
                        .catch((error) =>
                          setAlert(error instanceof ApiError ? error.message : "Could not approve that result."),
                        );
                    }}
                    onFolder={() => setStorageOpen(true)}
                    onWeb={() => {
                      setWebQuery(row.evidence?.original_filename || "");
                      setWebOpen(true);
                    }}
                    onNote={() => {
                      if (!saved && !runId) setAlert("Notes can only be added to saved queries. Please save the run first.");
                      else {
                        setNoteTarget(row.public_id);
                        setNoteOpen(true);
                      }
                    }}
                    onFullscreen={() => {
                      if (row.evidence?.public_id) {
                        setFullImage({ evidenceId: row.evidence.public_id });
                        setFullOpen(true);
                      }
                    }}
                  />
                ))}
            {!pending && !failed && preview?.status === "complete" && !resultRows.length ? (
              <p className="col-span-full text-[var(--text-muted)]">
                Search finished with no matches in the selected scope.
              </p>
            ) : !pending && !failed && !resultRows.length ? (
              <p className="col-span-full text-[var(--text-muted)]">Select a query or press Run to populate the grid.</p>
            ) : null}
          </div>
        </section>
      </div>

      {createOpen ? (
        <Dialog title="Create Case" onClose={() => setCreateOpen(false)}>
          <label className="block">
            Enter case name:
            <input className="field mt-1" placeholder="Case name..." value={newName} onChange={(e) => setNewName(e.target.value)} />
          </label>
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => setCreateOpen(false)}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" disabled={!newName.trim()} onClick={() => createCase.mutate()}>
              Create
            </button>
          </div>
        </Dialog>
      ) : null}

      {sourceOpen ? (
        <Dialog title="Evidence Source" onClose={() => setSourceOpen(false)}>
          <p className="mb-2">Corpora this case draws on:</p>
          <ul>
            {(corpora.data?.results ?? []).map((row) => (
              <li key={row.id}>{row.name}</li>
            ))}
          </ul>
          <div className="mt-3 flex justify-end">
            <button type="button" className="btn" onClick={() => setSourceOpen(false)}>
              Close
            </button>
          </div>
        </Dialog>
      ) : null}

      {storageOpen ? (
        <Dialog title="Case Storage" onClose={() => setStorageOpen(false)}>
          <p className="font-mono">cases/{caseId || "—"}/</p>
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => navigator.clipboard.writeText(`cases/${caseId}/`)}>
              Copy path
            </button>
            <button type="button" className="btn" onClick={() => setStorageOpen(false)}>
              Close
            </button>
          </div>
        </Dialog>
      ) : null}

      {clearOpen ? (
        <Dialog title="Clear Search" onClose={() => setClearOpen(false)}>
          <p>This will clear all search results and reset the search page.</p>
          <p>Your search parameters (Scope, Models, K, λ, α) will remain unchanged.</p>
          <p>Do you want to continue?</p>
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" className="btn btn-primary" onClick={clearSearch}>
              Clear
            </button>
            <button type="button" className="btn" onClick={() => setClearOpen(false)}>
              Cancel
            </button>
          </div>
        </Dialog>
      ) : null}

      {suggestOpen ? (
        <Dialog title="Suggested Text Queries" onClose={() => setSuggestOpen(false)}>
          <ul className="space-y-1">
            {SUGGESTIONS.map((item) => (
              <li key={item}>
                <button
                  type="button"
                  className="w-full rounded-[10px] px-2 py-1 text-left hover:bg-[var(--surface-muted)]"
                  title="Click to select"
                  onClick={() => {
                    addText(item);
                    setSuggestOpen(false);
                  }}
                >
                  {item}
                </button>
              </li>
            ))}
          </ul>
        </Dialog>
      ) : null}

      {webOpen ? (
        <Dialog title="Search Shoe on Web" onClose={() => setWebOpen(false)}>
          <p>
            Search query: {webSearch || "—"}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="btn" onClick={() => navigator.clipboard.writeText(webSearch)}>
              Copy
            </button>
            <a className="btn" href={`https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(webSearch)}`} target="_blank" rel="noreferrer">
              Search on eBay
            </a>
            <a className="btn" href={`https://www.jdsports.co.uk/search/${encodeURIComponent(webSearch)}/`} target="_blank" rel="noreferrer">
              Search on JD Sports
            </a>
            <button type="button" className="btn" onClick={() => setWebOpen(false)}>
              Close
            </button>
          </div>
        </Dialog>
      ) : null}

      {noteOpen ? (
        <Dialog title="Write Note" onClose={() => setNoteOpen(false)}>
          <textarea className="field h-28" value={noteBody} onChange={(e) => setNoteBody(e.target.value)} />
          <div className="mt-3 flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => setNoteOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                const target = resultRows[0];
                if (!target) return;
                api("/api/v1/review/notes/", {
                  method: "POST",
                  body: JSON.stringify({ result_public_id: target.public_id, body: noteBody, scope: "result" }),
                }).then(() => {
                  setNoteBody("");
                  setNoteOpen(false);
                });
              }}
            >
              Save
            </button>
          </div>
        </Dialog>
      ) : null}

      {fullOpen && (fullImage?.src || fullImage?.evidenceId || preview?.preview) ? (
        <div
          className="fixed inset-0 z-30 flex items-center justify-center bg-black/70 p-6"
          onClick={() => {
            setFullOpen(false);
            setFullImage(null);
          }}
        >
          {fullImage?.src || preview?.preview ? (
            <img src={fullImage?.src || preview?.preview} alt="" className="max-h-full max-w-full" />
          ) : (
            <SignedThumb evidenceId={fullImage?.evidenceId} variant="original" className="max-h-full max-w-full object-contain" />
          )}
        </div>
      ) : null}
    </div>
  );
}

function QueryList({
  label,
  rows,
  selected,
  onSelect,
  onChange,
  text,
}: {
  label: string;
  rows: LocalQuery[];
  selected: string;
  onSelect: (id: string) => void;
  onChange: (rows: LocalQuery[]) => void;
  text?: boolean;
}) {
  return (
    <div className="space-y-2">
      {label ? <p className="font-bold">{label}</p> : null}
      <div className="flex gap-2">
        <button type="button" className="btn max-w-[100px]" onClick={() => onChange(rows.map((row) => ({ ...row, checked: true })))}>
          Select All
        </button>
        <button type="button" className="btn max-w-[150px]" onClick={() => onChange(rows.map((row) => ({ ...row, checked: false })))}>
          Clear Selection
        </button>
      </div>
      <ul className="space-y-1">
        {rows.map((row) => (
          <li
            key={row.id}
            className={`flex items-center gap-2 rounded-[10px] px-2 py-1 ${selected === row.id ? "bg-[var(--select-blue)]" : ""}`}
          >
            <input
              type="checkbox"
              checked={row.checked}
              onChange={() => onChange(rows.map((item) => (item.id === row.id ? { ...item, checked: !item.checked } : item)))}
            />
            {row.preview ? <img src={row.preview} alt="" className="h-11 w-11 rounded-[10px] object-cover" /> : null}
            <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => onSelect(row.id)} title={row.text || row.filename}>
              {text ? row.text : `📷 ${row.filename}`}
            </button>
            <button
              type="button"
              className="h-8 w-8"
              title={text ? "Remove this text query" : "Withdraw Query"}
              onClick={() => onChange(rows.filter((item) => item.id !== row.id))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ResultActions({
  onFolder,
  onWeb,
  onNote,
  onFullscreen,
}: {
  onFolder: () => void;
  onWeb: () => void;
  onNote: () => void;
  onFullscreen: () => void;
}) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      <IconAction name="folder" label="Open Folder" size={30} onClick={(event) => { event.stopPropagation(); onFolder(); }} />
      <IconAction name="linkWeb" label="Open Web Link" size={30} onClick={(event) => { event.stopPropagation(); onWeb(); }} />
      <IconAction name="aiBrain" label="Describe (VLM)" size={30} disabled />
      <IconAction name="writeNote" label="Result Note" size={30} onClick={(event) => { event.stopPropagation(); onNote(); }} />
      <IconAction name="fullscreen" label="Fullscreen" size={30} onClick={(event) => { event.stopPropagation(); onFullscreen(); }} />
    </div>
  );
}

function ResultCard({
  row,
  layout,
  selected,
  rating,
  approvalMode,
  onSelect,
  onRate,
  onApprove,
  onFolder,
  onWeb,
  onNote,
  onFullscreen,
}: {
  row: ResultRow;
  layout: "list" | "grid";
  selected: boolean;
  rating: number;
  approvalMode: boolean;
  onSelect: () => void;
  onRate: (value: number) => void;
  onApprove: () => void;
  onFolder: () => void;
  onWeb: () => void;
  onNote: () => void;
  onFullscreen: () => void;
}) {
  const evidence = row.evidence;
  const name = evidence?.original_filename || "—";
  const path = evidence?.source_path || "—";
  const missing = evidence?.state === "missing";
  const pill = evidence?.corpus_code === "PreviousCases";
  const approved = row.approval_state === "approved" || row.approval_state === "countersigned";
  const shortName = name.length > 40 ? `${name.slice(0, 40)}…` : name;
  return (
    <article
      className={`relative rounded-[12px] border bg-white p-3 ${
        approved
          ? "border-[3px] border-[#10b981]"
          : approvalMode
            ? "hover:border-[3px] hover:border-[#10b981]"
            : selected
              ? "border-2 border-[var(--focus)]"
              : "border-[var(--border)]"
      }`}
      title={approvalMode ? (approved ? "Click to withdraw approval" : "Click to approve this image") : undefined}
      onClick={() => {
        if (approvalMode) onApprove();
        else onSelect();
      }}
    >
      {approved ? (
        <span className="absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded border-2 border-white bg-[#10b981] px-2 text-[11px] font-bold text-white">
          <AppIcon name="check" size={14} alt="" />
          APPROVED
        </span>
      ) : null}
      {layout === "list" ? (
        <div className="flex gap-3">
          <div className={`shrink-0 overflow-hidden rounded-[10px] border ${selected ? "border-2 border-[var(--focus)]" : "border-[var(--border)]"}`}>
            {missing ? (
              <div className="flex h-[170px] w-[240px] items-center justify-center text-[13px] font-semibold text-[var(--danger)]">
                Missing file
              </div>
            ) : (
              <SignedThumb
                evidenceId={evidence?.public_id}
                alt={name}
                className="h-[170px] w-[240px] object-contain"
              />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[13px] font-bold">
              #{row.rank}{"   "}confidence={confidenceScore(row.score_fused)}
            </p>
            {pill ? <span className="status-pill status-scanning">PreviousCases</span> : null}
            <p>{name}</p>
            <p className="truncate text-xs text-[var(--text-muted)]" title={path}>
              {path}
            </p>
            <p className="mt-1 flex items-center gap-2 text-[13px]">
              <span>Rating:</span>
              <StarRating value={rating} onChange={onRate} compact />
            </p>
            {missing ? null : <ResultActions onFolder={onFolder} onWeb={onWeb} onNote={onNote} onFullscreen={onFullscreen} />}
          </div>
        </div>
      ) : (
        <div>
          <div className={`mx-auto overflow-hidden rounded-[10px] border bg-[#f3f4f6] ${selected ? "border-2 border-[var(--focus)]" : "border-[var(--border)]"}`}>
            {missing ? (
              <div className="flex h-[160px] w-full items-center justify-center text-[13px] font-semibold text-[var(--danger)]">
                Missing file
              </div>
            ) : (
              <SignedThumb evidenceId={evidence?.public_id} alt={name} className="mx-auto h-[160px] w-[220px] object-contain" />
            )}
          </div>
          <p className="mt-2 w-full rounded border border-[var(--border)] px-2 py-1 text-[13px] font-bold">
            #{row.rank} conf={confidenceScore(row.score_fused)}
          </p>
          <p className="truncate" title={name}>
            {shortName}
          </p>
          <p className="mt-1 flex items-center gap-2 text-[13px]">
            <span>Rating:</span>
            <StarRating value={rating} onChange={onRate} compact />
          </p>
          {missing ? null : <ResultActions onFolder={onFolder} onWeb={onWeb} onNote={onNote} onFullscreen={onFullscreen} />}
        </div>
      )}
    </article>
  );
}

function Dialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="card w-full max-w-lg space-y-2" onClick={(event) => event.stopPropagation()}>
        <h2 className="text-sm font-bold">{title}</h2>
        {children}
      </div>
    </div>
  );
}
