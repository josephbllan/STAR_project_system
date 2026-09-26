import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

type Start = { secret: string; otpauth_uri: string; warning: string };

export function MfaEnrolPage() {
  const navigate = useNavigate();
  const [secret, setSecret] = useState<Start | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const start = useMutation({
    mutationFn: () => api<Start>("/api/v1/auth/mfa/totp/", { method: "POST" }),
    onSuccess: setSecret,
  });
  const confirm = useMutation({
    mutationFn: () =>
      api<{ recovery_codes: string[] }>("/api/v1/auth/mfa/totp/confirm/", {
        method: "POST",
        body: JSON.stringify({ code }),
      }),
    onSuccess: (body) => setCodes(body.recovery_codes),
    onError: () => setError("Verification failed. That code is not valid."),
  });

  return (
    <div className="mx-auto max-w-[640px] space-y-4">
      <div className="card">
        <p className="section-label">Step 1</p>
        <h2 className="text-sm font-bold">Scan this code</h2>
        <p className="text-[var(--text-muted)]">Use an authenticator app, then confirm the code.</p>
        {!secret ? (
          <button type="button" className="btn btn-primary mt-3" onClick={() => start.mutate()}>
            Show setup key
          </button>
        ) : (
          <div className="mt-3 space-y-2">
            <p className="rounded bg-[var(--warning)]/10 p-2 text-[var(--warning)]">{secret.warning}</p>
            <p className="break-all font-mono text-sm">{secret.secret}</p>
            <a className="text-[var(--brand-indigo)] underline" href={secret.otpauth_uri}>
              Open in authenticator
            </a>
          </div>
        )}
      </div>
      <div className="card">
        <p className="section-label">Step 2</p>
        <h2 className="text-sm font-bold">Enter the code from your app</h2>
        {error ? <p role="alert">{error}</p> : null}
        <input
          className="field mt-2 font-mono text-2xl tracking-[0.3em]"
          inputMode="numeric"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <button type="button" className="btn btn-primary mt-3" onClick={() => confirm.mutate()}>
          Confirm enrolment
        </button>
      </div>
      {codes ? (
        <div className="card">
          <p className="section-label">Step 3</p>
          <h2 className="text-sm font-bold">Save your recovery codes</h2>
          <ul className="mt-2 grid grid-cols-1 gap-1 font-mono md:grid-cols-2">
            {codes.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <button
            type="button"
            className="btn mt-3"
            onClick={() => void navigator.clipboard.writeText(codes.join("\n"))}
          >
            Copy all
          </button>
          <label className="mt-3 flex items-center gap-2">
            <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
            I have saved these codes
          </label>
          <button type="button" className="btn btn-primary mt-3" disabled={!saved} onClick={() => navigate("/datasets")}>
            Done
          </button>
        </div>
      ) : null}
    </div>
  );
}
