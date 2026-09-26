import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { LoginResult, api } from "../api";

export function MfaPage() {
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [error, setError] = useState("");

  async function verify(value: string) {
    setError("");
    try {
      await api<LoginResult>("/api/v1/auth/mfa/verify/", {
        method: "POST",
        body: JSON.stringify({ code: value }),
      });
      navigate("/datasets");
    } catch {
      setError("Verification failed. That code is not valid. Codes expire after 30 seconds.");
    }
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    await verify(code);
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--canvas)] p-4">
      <form onSubmit={onSubmit} className="card w-full max-w-[400px] p-8">
        <h1 className="text-lg font-black">Two-step verification</h1>
        <p className="mb-4 text-[var(--text-secondary)]">
          {recovery
            ? "Enter one of your unused recovery codes. Each code works once."
            : "Enter the 6-digit code from your authenticator app."}
        </p>
        {error ? (
          <p role="alert" className="mb-3 border-l-4 border-[var(--danger)] bg-[var(--danger-wash)] px-3 py-2">
            {error}
          </p>
        ) : null}
        <input
          className="field mb-4 font-mono text-2xl tracking-[0.3em]"
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(e) => {
            const next = e.target.value;
            setCode(next);
            if (!recovery && next.length === 6) void verify(next);
          }}
        />
        <button type="submit" className="btn btn-primary w-full">
          Verify
        </button>
        <button type="button" className="btn mt-3 w-full" onClick={() => setRecovery((v) => !v)}>
          {recovery ? "Use authenticator code" : "Use a recovery code instead"}
        </button>
        <button
          type="button"
          className="btn mt-2 w-full"
          onClick={async () => {
            await api("/api/v1/auth/logout/", { method: "POST" });
            navigate("/login");
          }}
        >
          Sign out
        </button>
      </form>
    </div>
  );
}
