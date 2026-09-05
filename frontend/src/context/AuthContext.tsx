import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { apiKeyStore, ApiError, listRuns } from "../api/client";

/**
 * Pulled forward from the Stage 6 plan's "Auth handling" section into
 * this phase, since the dashboard needs to work correctly against an
 * authenticated backend from the start, not bolted on later. Key lives
 * in sessionStorage only - never localStorage, matching this project's
 * own artifact-storage conventions elsewhere: a closed tab clears it,
 * by design, same spirit as the backend's own "disabled unless
 * configured" default posture toward auth (see api/auth.py).
 */

type AuthStatus = "checking" | "no-auth-required" | "needs-key" | "authenticated" | "invalid-key";

interface AuthContextValue {
  status: AuthStatus;
  setApiKey: (key: string) => Promise<boolean>;
  clearApiKey: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const SESSION_KEY = "reconciliation-ledger-api-key";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("checking");

  const probe = async () => {
    try {
      await listRuns();
      setStatus(apiKeyStore.get() ? "authenticated" : "no-auth-required");
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setStatus("needs-key");
      } else {
        // Not actually "no auth required" - could genuinely be that,
        // OR the backend is simply unreachable (a network-level
        // failure never reaches ApiError's branch above, since it
        // never gets a response to check .ok on). Deliberately not
        // adding a fourth status value for this: the dashboard's own
        // per-request error handling (see pages/Dashboard.tsx) already
        // surfaces "can't reach backend" clearly when the actual runs
        // fetch fails for the same reason, so nothing is silently
        // swallowed - this status only gates whether to show the API
        // key prompt, and "don't prompt for a key when the real
        // problem is connectivity" is the correct behavior either way.
        setStatus("no-auth-required");
      }
    }
  };

  useEffect(() => {
    // Real issue found in a comprehensive audit (see docs/DECISIONS.md):
    // this had no error handling at all - unlike the equivalent read
    // in NewRun.tsx's getRecentMerchants(). A storage-read failure here
    // is more severe than that one: some browser privacy
    // configurations block ALL storage API access, not just writes,
    // and this runs unconditionally on every mount with no error
    // boundary configured anywhere in the app - an uncaught exception
    // here would break the entire app before it ever renders anything
    // useful, not just one feature.
    try {
      const saved = sessionStorage.getItem(SESSION_KEY);
      if (saved) apiKeyStore.set(saved);
    } catch {
      // storage unavailable - proceed as if no key was ever saved,
      // same as a fresh session would look
    }
    probe();
  }, []);

  const setApiKey = async (key: string) => {
    apiKeyStore.set(key);
    try {
      await listRuns();
      // A storage-write failure here must not be conflated with an
      // invalid key - the key IS valid (listRuns() already succeeded
      // above), it just couldn't be PERSISTED for next time. Isolated
      // in its own try/catch so a storage failure can't fall through
      // to the outer catch and get mislabeled as "invalid-key".
      try {
        sessionStorage.setItem(SESSION_KEY, key);
      } catch {
        // couldn't persist - the key still works for this session,
        // the user will just need to re-enter it next time
      }
      setStatus("authenticated");
      return true;
    } catch (err) {
      apiKeyStore.set(null);
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setStatus("invalid-key");
      }
      return false;
    }
  };

  const clearApiKey = () => {
    apiKeyStore.set(null);
    // If removeItem throws, the in-memory key is already cleared above
    // but status would never update to reflect that without this
    // guard - wrapped so the UI state and the actual auth state can
    // never disagree with each other.
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      // storage unavailable - the in-memory key is already cleared,
      // which is what actually matters for this session
    }
    setStatus("needs-key");
  };

  return <AuthContext.Provider value={{ status, setApiKey, clearApiKey }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
