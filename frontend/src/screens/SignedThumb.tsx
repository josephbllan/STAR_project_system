import { useQuery } from "@tanstack/react-query";
import { AccessGrant, ApiError, api } from "../api";

export function SignedThumb({
  evidenceId,
  variant = "thumbnail",
  alt = "",
  className = "",
  width,
  height,
}: {
  evidenceId?: string;
  variant?: "thumbnail" | "original";
  alt?: string;
  className?: string;
  width?: number;
  height?: number;
}) {
  const grant = useQuery({
    queryKey: ["access-url", evidenceId, variant],
    queryFn: () =>
      api<AccessGrant>(`/api/v1/evidence/${evidenceId}/access-url/`, {
        method: "POST",
        body: JSON.stringify({ variant }),
      }),
    enabled: Boolean(evidenceId),
    staleTime: 45_000,
    retry: false,
  });

  if (!evidenceId) return <span className="text-[var(--text-muted)]">—</span>;
  if (grant.isError) {
    const missing = grant.error instanceof ApiError && grant.error.status === 404;
    return missing ? (
      <span className="text-[13px] font-semibold text-[var(--danger)]">Missing file</span>
    ) : (
      <button type="button" className="text-xs text-[var(--brand-indigo)]" onClick={() => grant.refetch()}>
        Reload image
      </button>
    );
  }
  if (!grant.data) return <span className="text-[var(--text-muted)]">—</span>;
  return <img src={grant.data.url} alt={alt} className={className} width={width} height={height} />;
}
