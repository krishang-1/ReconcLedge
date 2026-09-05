import { useNotifications } from "../context/NotificationContext";

/** Renders active toasts - mounted once in AppShell, visible on every
 * page regardless of which run or page they refer to. Click to
 * dismiss early; otherwise auto-clears (see NotificationContext). */
export function NotificationBar() {
  const { notifications, dismiss } = useNotifications();
  if (notifications.length === 0) return null;

  return (
    <div className="fixed top-20 right-6 z-50 flex flex-col gap-2 w-80 max-w-[calc(100vw-3rem)]">
      {notifications.map((n) => (
        <button
          key={n.id}
          onClick={() => dismiss(n.id)}
          className={`ledger-card text-left px-4 py-3 text-sm font-mono flex items-start gap-2.5 ${
            n.tone === "verified" ? "border-l-4 border-l-verified" : "border-l-4 border-l-flagged"
          }`}
        >
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 mt-1.5 ${n.tone === "verified" ? "bg-verified" : "bg-flagged"}`} />
          <span>{n.message}</span>
        </button>
      ))}
    </div>
  );
}
