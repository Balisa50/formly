"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "../lib/api";

export default function HistoryPage() {
  const [apps, setApps] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [clearingAll, setClearingAll] = useState(false);

  async function load() {
    try {
      const data = await api.listApplications();
      setApps(data);
    } catch {}
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleDelete(id: number) {
    setDeleting(id);
    try {
      await api.deleteApplication(id);
      setApps((prev) => prev.filter((a) => a.id !== id));
      if (expanded === id) setExpanded(null);
    } catch {}
    setDeleting(null);
  }

  async function handleClearAll() {
    if (!window.confirm("Delete all application history? This cannot be undone.")) return;
    setClearingAll(true);
    try {
      await Promise.all(apps.map((a) => api.deleteApplication(a.id)));
      setApps([]);
      setExpanded(null);
    } catch {}
    setClearingAll(false);
  }

  if (loading) return <p className="text-text-muted">Loading...</p>;

  const submitted = apps.filter((a) => a.status === "submitted").length;

  return (
    <>
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-bold">Application History</h1>
        {apps.length > 0 && (
          <button
            onClick={handleClearAll}
            disabled={clearingAll}
            className="text-xs text-red hover:text-red/80 disabled:opacity-50 transition-colors"
          >
            {clearingAll ? "Clearing..." : "Clear all"}
          </button>
        )}
      </div>
      <p className="text-sm text-text-muted mb-6">Every form you&apos;ve ever filled.</p>

      {apps.length === 0 ? (
        <div className="bg-surface rounded-xl border border-border p-10 text-center">
          <div className="w-14 h-14 rounded-full bg-accent/10 flex items-center justify-center mx-auto mb-4">
            <svg className="w-7 h-7 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <p className="font-medium mb-2">No applications yet</p>
          <p className="text-sm text-text-muted mb-5 max-w-sm mx-auto">
            Every form you fill will be tracked here — the URL, what was filled, and when.
          </p>
          <Link href="/fill" className="inline-flex bg-accent hover:bg-accent-hover text-white text-sm px-5 py-2.5 rounded-lg transition-colors">
            Fill a Form
          </Link>
        </div>
      ) : (
        <>
          {/* Stats */}
          <div className="grid grid-cols-3 gap-4 mb-6">
            <div className="bg-surface rounded-xl border border-border p-4">
              <p className="text-xs text-text-muted">Total</p>
              <p className="text-xl font-bold">{apps.length}</p>
            </div>
            <div className="bg-surface rounded-xl border border-border p-4">
              <p className="text-xs text-text-muted">Submitted</p>
              <p className="text-xl font-bold text-green">{submitted}</p>
            </div>
            <div className="bg-surface rounded-xl border border-border p-4">
              <p className="text-xs text-text-muted">Completion Rate</p>
              <p className="text-xl font-bold">
                {apps.length > 0 ? `${Math.round((submitted / apps.length) * 100)}%` : "—"}
              </p>
            </div>
          </div>

          {/* List */}
          <div className="space-y-2">
            {apps.map((app) => {
              const statusEmoji: Record<string, string> = {
                draft: "📝", filled: "✏️", previewed: "👀", submitted: "✅", failed: "❌",
              };
              const isExpanded = expanded === app.id;

              return (
                <div key={app.id} className="bg-surface rounded-xl border border-border">
                  <div className="flex items-center">
                    <button
                      onClick={() => setExpanded(isExpanded ? null : app.id)}
                      className="flex-1 flex items-center justify-between p-4 text-left hover:bg-surface-elevated transition-colors rounded-l-xl"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <span className="shrink-0">{statusEmoji[app.status] || "❓"}</span>
                        <div className="min-w-0">
                          <p className="text-sm font-medium truncate">{app.title || app.url}</p>
                          <p className="text-xs text-text-muted">{app.created_at?.slice(0, 10)}</p>
                        </div>
                      </div>
                      <span className="text-xs text-text-muted capitalize shrink-0 ml-3">{app.status}</span>
                    </button>

                    {/* Delete button */}
                    <button
                      onClick={() => handleDelete(app.id)}
                      disabled={deleting === app.id}
                      className="px-4 py-4 text-text-muted hover:text-red transition-colors disabled:opacity-40 shrink-0"
                      title="Remove"
                    >
                      {deleting === app.id ? (
                        <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                      ) : (
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      )}
                    </button>
                  </div>

                  {isExpanded && (
                    <div className="px-4 pb-4 border-t border-border pt-3">
                      <p className="text-xs text-text-muted mb-2 break-all">URL: {app.url}</p>
                      {app.submitted_at && (
                        <p className="text-xs text-text-muted mb-2">Submitted: {app.submitted_at}</p>
                      )}
                      {app.fields && Object.keys(app.fields).length > 0 && (
                        <div className="space-y-1 mt-2">
                          <p className="text-xs font-medium text-text-secondary">Filled Fields:</p>
                          {Object.entries(app.fields).map(([label, value]) => (
                            <div key={label} className="text-xs">
                              <span className="text-text-muted">{label}:</span>{" "}
                              <span className="text-text-secondary">{String(value).slice(0, 100)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}
