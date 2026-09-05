import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

export interface Notification {
  id: string;
  message: string;
  tone: "verified" | "flagged";
}

interface NotificationContextValue {
  notifications: Notification[];
  notify: (message: string, tone: Notification["tone"]) => void;
  dismiss: (id: string) => void;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

/** Global toast/notification state - added alongside RunsProvider (see
 * that file) to surface run status changes (completed/failed) from
 * anywhere in the app, not just while looking directly at that run's
 * own page. Deliberately minimal: a message and a tone (verified/
 * flagged, reusing the same two-tone vocabulary as everything else in
 * this design system rather than inventing a third "info" color),
 * auto-dismissing after a few seconds or on click. */
export function NotificationProvider({ children }: { children: ReactNode }) {
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const dismiss = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }, []);

  const notify = useCallback(
    (message: string, tone: Notification["tone"]) => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      setNotifications((prev) => [...prev, { id, message, tone }]);
      setTimeout(() => dismiss(id), 7000);
    },
    [dismiss],
  );

  return (
    <NotificationContext.Provider value={{ notifications, notify, dismiss }}>{children}</NotificationContext.Provider>
  );
}

export function useNotifications() {
  const ctx = useContext(NotificationContext);
  if (!ctx) throw new Error("useNotifications must be used within a NotificationProvider");
  return ctx;
}
