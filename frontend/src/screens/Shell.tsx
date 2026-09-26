import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext } from "react-router-dom";
import { AppLogo } from "../assets/AppIcon";
import { Session, TaskRow, api } from "../api";
import { TASKS_QUERY, isInFlight } from "../taskProgress";
import { subscribeHeaderContext } from "../shellContext";
import { TaskCentre } from "./TaskCentre";

const NAV = [
  { to: "/workers", label: "Device" },
  { to: "/video", label: "Video", disabled: true },
  { to: "/datasets", label: "Datasets" },
  { to: "/register-evidence", label: "Index" },
  { to: "/search", label: "Search" },
  { to: "/cases", label: "Sessions" },
  { to: "/review", label: "Review" },
  { to: "/analytics", label: "Analytics", disabled: true },
  { to: "/settings", label: "Settings" },
  { to: "/audit", label: "Audit" },
];

const TITLES: Record<string, string> = {
  "/workers": "Device",
  "/video": "Video",
  "/datasets": "Datasets",
  "/index": "Index (Register Evidence)",
  "/register-evidence": "Index (Register Evidence)",
  "/search": "Search",
  "/cases": "Sessions",
  "/review": "Review",
  "/analytics": "Analytics",
  "/settings": "Settings",
  "/audit": "Audit",
  "/admin": "Administration",
  "/account/mfa/enrol": "Multi-factor authentication",
};

export function Shell() {
  const session = useOutletContext<Session>();
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [tasksOpen, setTasksOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [headerContext, setHeaderContext] = useState("");
  useEffect(() => {
    return subscribeHeaderContext(setHeaderContext);
  }, []);
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: () => api<{ results: TaskRow[] }>(TASKS_QUERY),
    refetchInterval: 8000,
  });
  const active = (tasks.data?.results ?? []).filter((t) => isInFlight(t.status)).length;
  const title =
    TITLES[location.pathname] ||
    (location.pathname.startsWith("/cases/") ? "Sessions" : "ShoeRAG");
  const display = [session.user.first_name, session.user.last_name].filter(Boolean).join(" ") ||
    session.user.username;
  const isAdmin = session.user.role === "administrator";
  const isAuditor = session.user.role === "auditor";

  async function signOut() {
    await api("/api/v1/auth/logout/", { method: "POST" });
    client.clear();
    navigate("/login");
  }

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-[200px] shrink-0 flex-col bg-[var(--nav-bg)] p-2 md:flex">
        <div className="mb-1 flex h-[90px] items-center justify-center">
          <AppLogo className="max-h-[80px] max-w-[176px] rounded-lg bg-white p-1" />
        </div>
        <nav className="flex flex-1 flex-col gap-1.5">
          {NAV.map((item) =>
            item.disabled || (isAuditor && item.to !== "/audit") ? (
              <span
                key={item.to}
                title={item.disabled ? "Planned for a later release." : undefined}
                className="rounded-[10px] px-2.5 py-2.5 text-[13px] text-white/30"
              >
                {item.label}
              </span>
            ) : (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  [
                    "rounded-[10px] px-2.5 py-2.5 text-[13px] text-white/90",
                    isActive ? "border-l-4 border-[#4f46e5] bg-[rgba(79,70,229,0.22)] pl-1.5" : "hover:bg-white/10",
                  ].join(" ")
                }
              >
                {item.label}
              </NavLink>
            ),
          )}
        </nav>
        {isAdmin ? (
          <Link
            to="/admin"
            className="mb-2 rounded-[20px] border border-white/20 bg-white/10 px-3 py-2 text-center text-white"
          >
            Administration
          </Link>
        ) : null}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 bg-[var(--brand-header)] px-3 py-2 text-white">
          <h1 className="text-lg font-extrabold">{title}</h1>
          <span className="flex-1 text-xs text-white/85">{headerContext}</span>
          <button type="button" className="rounded-[20px] border border-white/20 bg-white/10 px-3 py-1" onClick={() => setTasksOpen(true)}>
            Tasks{active ? ` (${active})` : ""} ▾
          </button>
          <div className="relative">
            <button
              type="button"
              className="rounded-[20px] border border-white/20 bg-white/10 px-3 py-1"
              onClick={() => setAccountOpen((open) => !open)}
            >
              {display} · {session.user.role} ▾
            </button>
            {accountOpen ? (
              <div className="absolute right-0 z-20 mt-1 w-56 rounded-xl border bg-white py-1 text-[var(--text)] shadow-[0_12px_32px_rgba(17,24,39,0.18)]">
                <Link className="block px-3 py-2 hover:bg-[var(--surface-muted)]" to="/account/mfa/enrol">
                  Multi-factor authentication
                </Link>
                <button type="button" className="block w-full px-3 py-2 text-left hover:bg-[var(--surface-muted)]" onClick={signOut}>
                  Sign out
                </button>
              </div>
            ) : null}
          </div>
        </header>
        <main className="flex-1 p-2 md:px-3">
          <Outlet context={session} />
        </main>
      </div>
      {tasksOpen ? <TaskCentre onClose={() => setTasksOpen(false)} /> : null}
    </div>
  );
}
