import { useQuery } from "@tanstack/react-query";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { ApiError, Session, api } from "../api";

export function RequireSession() {
  const location = useLocation();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: () => api<Session>("/api/v1/auth/session/"),
    retry: false,
  });

  if (session.isPending) {
    return <p className="p-6">Loading…</p>;
  }
  if (session.isError) {
    const error = session.error as ApiError;
    const slug = String(error.body?.type || "");
    if (slug.includes("mfa-incomplete")) {
      return <Navigate to="/login/mfa" replace />;
    }
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  const path = location.pathname;
  const onMfa = path === "/login/mfa" || path === "/account/mfa/enrol";
  if (session.data.mfa_required && !onMfa) {
    return <Navigate to={session.data.enrol_required ? "/account/mfa/enrol" : "/login/mfa"} replace />;
  }
  return <Outlet context={session.data} />;
}
