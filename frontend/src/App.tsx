import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { AuthProvider } from "./context/AuthContext";
import { NotificationProvider } from "./context/NotificationContext";
import { RunsProvider } from "./context/RunsContext";
import { Audit } from "./pages/Audit";
import { Dashboard } from "./pages/Dashboard";
import { Merchants } from "./pages/Merchants";
import { NewRun } from "./pages/NewRun";
import { RunDetail } from "./pages/RunDetail";
import { Settings } from "./pages/Settings";
import { Tools } from "./pages/Tools";

/**
 * A later UI rework (see docs/DECISIONS.md) added the sidebar +
 * notification system - `NotificationProvider` wraps `RunsProvider`
 * deliberately in that order, since `RunsProvider` calls
 * `useNotifications()` internally to raise a toast when a run
 * completes or fails, and would throw if mounted outside a
 * `NotificationProvider`.
 */
export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <NotificationProvider>
          <RunsProvider>
            <Routes>
              <Route element={<AppShell />}>
                <Route path="/" element={<Dashboard />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/runs/new" element={<NewRun />} />
                <Route path="/runs/:runId" element={<RunDetail />} />
                <Route path="/audit" element={<Audit />} />
                <Route path="/tools" element={<Tools />} />
                <Route path="/merchants" element={<Merchants />} />
              </Route>
            </Routes>
          </RunsProvider>
        </NotificationProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
