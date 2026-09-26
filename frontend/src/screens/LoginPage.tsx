import { FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AppLogo } from "../assets/AppIcon";
import { ApiError, LoginResult, api } from "../api";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState<{ title: string; body: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api("/api/v1/auth/csrf/");
      const result = await api<LoginResult>("/api/v1/auth/login/", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      if (result.mfa_required && result.enrol_required) {
        navigate("/account/mfa/enrol");
        return;
      }
      if (result.mfa_required) {
        navigate("/login/mfa");
        return;
      }
      const intended = (location.state as { from?: string } | null)?.from;
      const role = result.user?.role;
      navigate(intended || (role === "auditor" ? "/audit" : "/datasets"));
    } catch (caught) {
      const failure = caught as ApiError;
      if (failure.status === 423) {
        setError({ title: "Account locked", body: "Too many failed attempts. Try again in 15 minutes, or contact an administrator." });
      } else if (failure.status === 429) {
        setError({ title: "Too many attempts", body: "Too many attempts. Please wait a few seconds." });
      } else if (failure.status === 503) {
        setError({ title: "Unavailable", body: "The service is starting. Try again in a few seconds." });
      } else {
        setError({
          title: "Authentication failed",
          body: "Check your username and password, then try again.",
        });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--canvas)] p-4">
      <form onSubmit={onSubmit} className="card w-full max-w-[400px] p-8">
        <AppLogo className="mx-auto mb-4 h-[96px] w-auto" />
        <h1 className="text-center text-lg font-black">ShoeRAG</h1>
        <p className="mb-6 text-center text-[var(--text-muted)]">Forensic footwear retrieval</p>
        {error ? (
          <p role="alert" className="mb-4 border-l-4 border-[var(--danger)] bg-[var(--danger-wash)] px-3 py-2 text-[var(--danger-dark)]">
            <strong>{error.title}.</strong> {error.body}
          </p>
        ) : null}
        <label className="mb-3 block text-sm">
          Username
          <input
            className="field mt-1"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
          />
        </label>
        <label className="mb-4 block text-sm">
          Password
          <span className="mt-1 flex gap-2">
            <input
              className="field"
              type={reveal ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
            <button type="button" className="btn" onClick={() => setReveal((v) => !v)}>
              {reveal ? "Hide" : "Show"}
            </button>
          </span>
        </label>
        <button type="submit" className="btn btn-primary w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
