import { FormEvent, ReactNode, useMemo, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, CorpusRow, EvidenceRow, MountRow, Session, api, rowsOf } from "../api";

function folderName(path: string, label: string): string {
  if (label) return label;
  const trimmed = path.replace(/[\\/]+$/, "");
  return trimmed.split(/[\\/]/).pop() || path;
}

function firstError(body: Record<string, unknown>, fallback: string): string {
  for (const value of Object.values(body)) {
    if (Array.isArray(value) && typeof value[0] === "string") return value[0];
    if (typeof value === "string") return value;
  }
  return fallback;
}

function formatScanAt(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${day}/${month} ${hours}:${minutes}`;
}

function statusLabel(status: string | null, scanning: boolean): string {
  if (scanning) return "Scanning…";
  if (!status) return "—";
  if (status === "ok") return "OK";
  if (status === "partial") return "Partial";
  if (status === "failed") return "Failed";
  return status;
}

function statusClass(status: string | null, scanning: boolean): string {
  if (scanning) return "status-pill status-scanning";
  if (status === "ok") return "status-pill status-ok";
  if (status === "partial") return "status-pill status-partial";
  if (status === "failed") return "status-pill status-failed";
  return "status-pill";
}

export function DatasetsPage() {
  const session = useOutletContext<Session | undefined>();
  const isAdmin = session?.user.role === "administrator";
  const client = useQueryClient();
  const corpora = useQuery({
    queryKey: ["corpora"],
    queryFn: () => api<{ results: CorpusRow[] }>("/api/v1/corpora/?page_size=100"),
  });
  const mounts = useQuery({
    queryKey: ["mounts"],
    queryFn: () => api<{ results: MountRow[] }>("/api/v1/mounts/?page_size=100"),
    refetchInterval: 8000,
  });
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [detail, setDetail] = useState(false);
  const [storageOpen, setStorageOpen] = useState(false);
  const [summary, setSummary] = useState<MountRow[] | null>(null);
  const [corpus, setCorpus] = useState("");
  const [path, setPath] = useState("");
  const [label, setLabel] = useState("");
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [filterCorpus, setFilterCorpus] = useState("");
  const [filterStatus, setFilterStatus] = useState<string[]>([]);
  const [filterPath, setFilterPath] = useState("");
  const [scanning, setScanning] = useState<number[]>([]);

  const byId = useMemo(() => {
    const map = new Map<number, CorpusRow>();
    for (const row of corpora.data?.results ?? []) map.set(row.id, row);
    return map;
  }, [corpora.data]);

  const addFolder = useMutation({
    mutationFn: () =>
      api<MountRow>("/api/v1/mounts/", {
        method: "POST",
        body: JSON.stringify({ corpus: Number(corpus), path, label }),
      }),
    onSuccess: (row) => {
      setAdding(false);
      setPath("");
      setLabel("");
      setScanning((ids) => [...ids, row.id]);
      setMessage("Folder added. Scanning every subfolder, then encoding.");
      client.invalidateQueries({ queryKey: ["mounts"] });
      client.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (error: unknown) => {
      setMessage(error instanceof ApiError ? firstError(error.body, error.message) : "Could not add that folder.");
    },
  });

  const scan = useMutation({
    mutationFn: async (ids: number[]) => {
      for (const id of ids) {
        await api(`/api/v1/mounts/${id}/scan/`, { method: "POST" });
      }
      return ids;
    },
    onSuccess: (ids) => {
      setScanning((current) => [...current, ...ids]);
      setMessage("");
      client.invalidateQueries({ queryKey: ["tasks"] });
      client.invalidateQueries({ queryKey: ["mounts"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) =>
      api(`/api/v1/mounts/${id}/`, { method: "PATCH", body: JSON.stringify({ is_enabled: false }) }),
    onSuccess: () => {
      setRemoving(false);
      setSelected(null);
      client.invalidateQueries({ queryKey: ["mounts"] });
    },
  });

  function onAdd(event: FormEvent) {
    event.preventDefault();
    if (corpus && path.trim()) addFolder.mutate();
  }

  const allRows = (mounts.data?.results ?? []).filter((row) => row.is_enabled !== false);
  const rows = allRows.filter((row) => {
    if (filterCorpus && String(row.corpus) !== filterCorpus) return false;
    if (filterStatus.length && !filterStatus.includes(row.last_scan_status || "none")) return false;
    if (filterPath && !row.path.toLowerCase().includes(filterPath.toLowerCase())) return false;
    return true;
  });
  const chosen = rows.find((row) => row.id === selected) ?? null;
  const hasOk = allRows.some((row) => row.last_scan_status === "ok");

  function toggleStatus(value: string) {
    setFilterStatus((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    );
  }

  function onRescan() {
    const ids = selected != null ? [selected] : allRows.filter((row) => row.is_enabled).map((row) => row.id);
    if (!ids.length) return;
    scan.mutate(ids, {
      onSuccess: () => {
        const latest = rowsOf(client.getQueryData<{ results: MountRow[] }>(["mounts"]));
        setSummary(latest.length ? latest : allRows);
      },
    });
  }

  return (
    <div className="space-y-2 p-2">
      <section className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
          Add Folder
        </button>
        <button
          type="button"
          className="btn"
          disabled={selected == null || !isAdmin}
          title={isAdmin ? undefined : "Administrator only"}
          onClick={() => selected != null && setRemoving(true)}
        >
          Remove
        </button>
        <button type="button" className="btn" onClick={onRescan} disabled={scan.isPending}>
          Rescan
        </button>
        <span className="flex-1" />
        <button
          type="button"
          className="btn btn-primary"
          disabled={selected == null}
          onClick={() => setStorageOpen(true)}
        >
          Open in Storage
        </button>
      </section>

      <section className="flex flex-wrap items-end gap-2">
        <label>
          Corpus
          <select className="field mt-1" value={filterCorpus} onChange={(e) => setFilterCorpus(e.target.value)}>
            <option value="">All</option>
            {(corpora.data?.results ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
        <div>
          <p className="mb-1">Status</p>
          <div className="flex flex-wrap gap-1">
            {[
              ["ok", "OK"],
              ["partial", "Partial"],
              ["failed", "Failed"],
              ["none", "—"],
            ].map(([value, labelText]) => (
              <button
                key={value}
                type="button"
                className={`chip ${filterStatus.includes(value) ? "chip-on" : ""}`}
                onClick={() => toggleStatus(value)}
              >
                {labelText}
              </button>
            ))}
          </div>
        </div>
        <label className="min-w-[220px] flex-1">
          <span className="sr-only">Filter by path</span>
          <input
            className="field mt-1"
            value={filterPath}
            onChange={(e) => setFilterPath(e.target.value)}
            placeholder="Filter by path…"
          />
        </label>
      </section>

      {message ? <p className="text-xs text-[var(--text-muted)]">{message}</p> : null}

      <section className="card overflow-x-auto p-0">
        <table className="w-full text-left">
          <thead className="bg-[var(--surface-muted)] text-[13px] font-bold text-[var(--text-secondary)]">
            <tr>
              <th className="px-3 py-2">Path</th>
              <th className="px-3 py-2 whitespace-nowrap">Corpus</th>
              <th className="px-3 py-2 whitespace-nowrap">Images</th>
              <th className="px-3 py-2 whitespace-nowrap">Last Scan</th>
              <th className="px-3 py-2 whitespace-nowrap" title="Last folder scan, not indexing">
                Last scan
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const busy = scanning.includes(row.id) && !row.last_scan_at;
              return (
                <tr
                  key={row.id}
                  className={`h-16 cursor-pointer ${selected === row.id ? "bg-[var(--select-blue)]" : ""}`}
                  onClick={() => setSelected(row.id)}
                  onDoubleClick={() => {
                    setSelected(row.id);
                    setDetail(true);
                  }}
                >
                  <td className="px-3">
                    <div className="flex items-center gap-3">
                      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#dbeafe] text-xs font-extrabold">
                        DS
                      </span>
                      <div>
                        <p className="text-sm font-extrabold">{folderName(row.path, row.label)}</p>
                        <p className="text-xs text-[var(--text-muted)]" title={row.path}>
                          {row.path}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="px-3 font-bold">{row.corpus_code || byId.get(row.corpus)?.name || "—"}</td>
                  <td className="px-3">
                    {row.last_scan_at ? row.last_scan_files_seen.toLocaleString() : "—"}
                  </td>
                  <td className="px-3 text-xs">{formatScanAt(row.last_scan_at)}</td>
                  <td className="px-3">
                    <span
                      className={statusClass(row.last_scan_status, busy)}
                      title={
                        row.last_scan_error_count
                          ? `Last folder scan: ${row.last_scan_error_count} errors (not the index)`
                          : "Last folder scan (not the index)"
                      }
                    >
                      {statusLabel(row.last_scan_status, busy)}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length ? (
          <p className="py-6 text-center">No folders yet. Add a local folder to Ecom, NDFsim or PreviousCases.</p>
        ) : null}
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Last scan is the folder walk, not the index. A red Failed row can still have every image indexed.
          The three demo paths with 0 images can be Removed.
        </p>
      </section>

      {hasOk ? (
        <p>
            <Link className="font-extrabold text-[var(--brand-header)]" to="/register-evidence">
            Index these folders →
          </Link>
        </p>
      ) : null}

      {adding ? (
        <Modal title="Add Folder" onClose={() => setAdding(false)}>
          <form className="space-y-3" onSubmit={onAdd}>
            <label className="block">
              Corpus
              <select className="field mt-1" value={corpus} onChange={(e) => setCorpus(e.target.value)} required>
                <option value="">Select</option>
                {(corpora.data?.results ?? []).map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              Storage prefix
              <input
                className="field mt-1"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="evidence/ecom/catalogue-2024"
                required
              />
            </label>
            <label className="block">
              Label
              <input className="field mt-1" value={label} onChange={(e) => setLabel(e.target.value)} />
            </label>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn" onClick={() => setAdding(false)}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={!corpus || !path.trim() || addFolder.isPending}>
                Add Folder
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {removing && chosen ? (
        <Modal title="Remove mount" onClose={() => setRemoving(false)}>
          <p>This removes the folder from scanning. Evidence already registered from it is retained and remains searchable.</p>
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => setRemoving(false)}>
              Cancel
            </button>
            <button type="button" className="btn btn-danger" onClick={() => remove.mutate(chosen.id)}>
              Remove
            </button>
          </div>
        </Modal>
      ) : null}

      {detail && chosen ? (
        <Modal title="Mount detail" onClose={() => setDetail(false)}>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <dt className="text-[var(--text-muted)]">Prefix</dt>
            <dd className="font-mono">{chosen.path}</dd>
            <dt className="text-[var(--text-muted)]">Corpus</dt>
            <dd>{chosen.corpus_code || byId.get(chosen.corpus)?.name}</dd>
            <dt className="text-[var(--text-muted)]">Added</dt>
            <dd>{chosen.created_at ? new Date(chosen.created_at).toLocaleString() : "—"}</dd>
            <dt className="text-[var(--text-muted)]">Last scan</dt>
            <dd>{statusLabel(chosen.last_scan_status, false)}</dd>
            <dt className="text-[var(--text-muted)]">Files seen</dt>
            <dd>{chosen.last_scan_at ? chosen.last_scan_files_seen.toLocaleString() : "—"}</dd>
            <dt className="text-[var(--text-muted)]">Errors</dt>
            <dd>{chosen.last_scan_error_count}</dd>
          </dl>
          <div className="mt-4 flex justify-end">
            <button
              type="button"
              className="btn"
              onClick={() => navigator.clipboard.writeText(chosen.path)}
            >
              Copy path
            </button>
          </div>
        </Modal>
      ) : null}

      {storageOpen && chosen ? <StorageBrowser mount={chosen} onClose={() => setStorageOpen(false)} /> : null}

      {summary ? (
        <Modal title="Rescan Summary" onClose={() => setSummary(null)}>
          <p className="mb-3 font-extrabold">
            {summary.every((row) => row.last_scan_status === "ok")
              ? "100% healthy dataset"
              : "Dataset issues found"}
          </p>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-[var(--text-secondary)]">
                <th>Folder</th>
                <th>Status</th>
                <th>Images</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {summary.map((row) => (
                <tr key={row.id}>
                  <td>{folderName(row.path, row.label)}</td>
                  <td>{statusLabel(row.last_scan_status, false)}</td>
                  <td>{row.last_scan_at ? row.last_scan_files_seen.toLocaleString() : "—"}</td>
                  <td>
                    {row.last_scan_status === "failed"
                      ? "This folder path does not exist.\n\nUse 'Copy path' to investigate."
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => setSummary(null)}>
              Close
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="card w-full max-w-lg space-y-3" onClick={(event) => event.stopPropagation()}>
        <h2 className="text-sm font-bold">{title}</h2>
        {children}
      </div>
    </div>
  );
}

function StorageBrowser({ mount, onClose }: { mount: MountRow; onClose: () => void }) {
  const evidence = useQuery({
    queryKey: ["evidence", "mount", mount.id],
    queryFn: () => api<{ results: EvidenceRow[] }>(`/api/v1/evidence/?mount=${mount.id}&page_size=100`),
  });
  return (
    <Modal title="Open in Storage" onClose={onClose}>
      <p className="font-mono text-xs text-[var(--text-muted)]">{mount.path}</p>
      <ul className="mt-2 max-h-80 space-y-1 overflow-auto">
        {(evidence.data?.results ?? []).map((row) => (
          <li key={row.public_id} className="flex justify-between gap-2 rounded-[10px] px-2 py-1 even:bg-[var(--surface-muted)]">
            <span className="truncate font-extrabold">{row.original_filename || row.public_id}</span>
            <span className="text-xs text-[var(--text-muted)]">{row.state}</span>
          </li>
        ))}
        {!evidence.data?.results.length ? <p>No registered objects at this prefix.</p> : null}
      </ul>
      <div className="flex justify-end">
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
