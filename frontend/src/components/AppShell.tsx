import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { getHealth } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { useRuns } from "../context/RunsContext";
import { NotificationBar } from "./NotificationBar";
import { RunsSidebar } from "./RunsSidebar";
import { StatusBadge } from "./StatusBadge";
import { truncateRunId } from "../lib/format";

const NAV_LINKS = [
  { to: "/tools", label: "Tools" },
  { to: "/audit", label: "Audit" },
  { to: "/merchants", label: "Merchants" },
  { to: "/settings", label: "Settings" },
];

/**
 * Persistent header + sidebar + notification shell.
 *
 * Header aligned to the sidebar's own left inset (px-4, matching
 * RunsSidebar.tsx) instead of an independently centered max-width
 * container - the mismatch between the two was a real visible gap
 * between the logo and the sidebar below it. The health check moved
 * out of the header entirely and into the sidebar's own footer - one
 * less thing competing for header space, and it fills space the
 * sidebar wasn't using.
 */
export function AppShell() {
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const { status } = useAuth();
  const location = useLocation();

  useEffect(() => {
    const check = () => getHealth().then((h) => setHealthy(h.status === "ok")).catch(() => setHealthy(false));
    check();
    const interval = setInterval(check, 15_000);
    return () => clearInterval(interval);
  }, []);

  // Close the drawer on navigation and on Escape - a menu that survives
  // its own link click, or that only a backdrop-tap can dismiss, reads
  // as broken rather than considered.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);

  return (
    <div className="min-h-svh flex flex-col">
      <header className="border-b border-rule bg-ink-raised/80 backdrop-blur-sm sticky top-0 z-20">
        <div className="px-4 py-4 flex items-center justify-between">
          <NavLink to="/" className="flex items-baseline gap-2 group">
            <span className="w-1.5 h-1.5 rounded-full bg-accent group-hover:scale-125 transition-transform" />
            <span className="font-heading text-xl font-semibold text-paper-text tracking-tight">ReconcLedge</span>
          </NavLink>

          {/* Full nav - hidden below lg, same breakpoint RunsSidebar
              switches off at, so the mobile drawer below is the only
              nav surface exactly when the sidebar also disappears. */}
          <div className="hidden lg:flex items-center gap-6 font-mono text-sm">
            {status === "authenticated" && <span className="text-verified">key set</span>}
            {NAV_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  `relative pb-1 transition-colors ${
                    isActive
                      ? "text-paper-text after:absolute after:left-0 after:right-0 after:-bottom-[17px] after:h-[2px] after:bg-accent"
                      : "text-paper-text/50 hover:text-paper-text/80"
                  }`
                }
              >
                {link.label}
              </NavLink>
            ))}
          </div>

          {/* Hamburger toggle - the only way to reach nav links or the
              runs list below the lg breakpoint, so it has to work. */}
          <button
            type="button"
            onClick={() => setMobileNavOpen((v) => !v)}
            aria-label={mobileNavOpen ? "Close menu" : "Open menu"}
            aria-expanded={mobileNavOpen}
            className="lg:hidden -mr-2 p-2 text-paper-text/70 hover:text-paper-text transition-colors"
          >
            <span className="sr-only">{mobileNavOpen ? "Close menu" : "Open menu"}</span>
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round">
              {mobileNavOpen ? (
                <>
                  <line x1="5" y1="5" x2="19" y2="19" />
                  <line x1="19" y1="5" x2="5" y2="19" />
                </>
              ) : (
                <>
                  <line x1="3" y1="6" x2="21" y2="6" />
                  <line x1="3" y1="12" x2="21" y2="12" />
                  <line x1="3" y1="18" x2="21" y2="18" />
                </>
              )}
            </svg>
          </button>
        </div>
      </header>

      <MobileNav open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} authenticated={status === "authenticated"} />

      <div className="flex-1 flex">
        <RunsSidebar healthy={healthy} />
        <main className="flex-1 min-w-0">
          <Outlet />
        </main>
      </div>

      <NotificationBar />
    </div>
  );
}

/**
 * Slide-down drawer covering both gaps that open up below `lg`: the
 * header's nav links (hidden alongside the desktop nav) and the runs
 * list (RunsSidebar hides itself below `lg` with no replacement of its
 * own). Reuses RunsContext's shared polling rather than fetching
 * independently, same as RunsSidebar.
 */
function MobileNav({ open, onClose, authenticated }: { open: boolean; onClose: () => void; authenticated: boolean }) {
  const { runs } = useRuns();
  const recent = runs.slice(0, 5);

  return (
    <div
      className={`lg:hidden fixed inset-0 z-10 transition-opacity ${open ? "opacity-100" : "opacity-0 pointer-events-none"}`}
      aria-hidden={!open}
    >
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div
        className={`absolute top-0 left-0 right-0 bg-ink-raised border-b border-rule pt-20 pb-6 px-4 shadow-2xl shadow-black/50 transition-transform duration-200 ${
          open ? "translate-y-0" : "-translate-y-4"
        }`}
      >
        {authenticated && <p className="text-verified font-mono text-sm mb-4 px-2">key set</p>}
        <nav className="flex flex-col gap-1 mb-6">
          {NAV_LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) =>
                `font-mono text-sm px-2 py-2.5 rounded-md transition-colors ${
                  isActive ? "bg-paper-text/10 text-paper-text" : "text-paper-text/70 hover:bg-paper-text/5"
                }`
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>

        <p className="font-mono text-[11px] uppercase tracking-wide text-paper-text/40 mb-3 px-2">Recent processes</p>
        {recent.length === 0 ? (
          <p className="text-paper-text/25 font-mono text-xs px-2">No runs yet.</p>
        ) : (
          <ul className="space-y-1">
            {recent.map((run) => (
              <li key={run.run_id}>
                <NavLink to={`/runs/${run.run_id}`} className="flex items-center justify-between gap-2 px-2 py-2 rounded-md hover:bg-paper-text/5 transition-colors">
                  <span className="font-mono text-xs text-paper-text/70 truncate">{truncateRunId(run.run_id)}</span>
                  <StatusBadge status={run.status} />
                </NavLink>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
