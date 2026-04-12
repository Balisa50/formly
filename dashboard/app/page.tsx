"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "./lib/api";

export default function DashboardPage() {
  const [stats, setStats] = useState<any>(null);
  const [completeness, setCompleteness] = useState<number>(0);
  const [online, setOnline] = useState(true);

  useEffect(() => {
    Promise.all([api.getStats(), api.getCompleteness()])
      .then(([s, c]) => { setStats(s); setCompleteness(c.completeness); })
      .catch(() => setOnline(false));
  }, []);

  if (!online) {
    return (
      <div className="bg-surface rounded-xl border border-border p-8 text-center">
        <h1 className="text-2xl font-bold mb-2">Formly</h1>
        <p className="text-text-muted">
          Backend offline — start with{" "}
          <code className="text-accent">python -m uvicorn formly.api:app --reload</code>
        </p>
      </div>
    );
  }

  return (
    <>
      <h1 className="text-2xl font-bold mb-2">Dashboard</h1>
      <p className="text-text-muted text-sm mb-6">Fill once. Apply anywhere.</p>

      {/* Profile completeness */}
      <div className="bg-surface rounded-xl border border-border p-5 mb-6">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-text-secondary">Profile Completeness</span>
          <span className="text-sm font-bold">{completeness}%</span>
        </div>
        <div className="w-full h-2 bg-background rounded-full overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-all"
            style={{ width: `${completeness}%` }}
          />
        </div>
        {completeness < 100 && (
          <Link href="/profile" className="text-xs text-accent hover:text-accent-hover mt-2 inline-block">
            Complete your profile
          </Link>
        )}
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-8">
          <StatCard label="Applications" value={stats.total_applications} />
          <StatCard label="Submitted" value={stats.submitted} accent={stats.submitted > 0} />
          <StatCard label="Profile Fields" value={stats.profile_fields} />
          <StatCard label="Work Experience" value={stats.work_entries} />
          <StatCard label="Education" value={stats.education_entries} />
          <StatCard label="Skills" value={stats.skills_count} />
        </div>
      )}

      {/* Quick actions */}
      <div className="grid md:grid-cols-2 gap-4">
        <Link href="/fill" className="bg-surface rounded-xl border border-border p-5 hover:bg-surface-elevated transition-colors">
          <h3 className="font-semibold mb-1">Fill a Form</h3>
          <p className="text-sm text-text-muted">Paste a URL and let Formly handle the rest</p>
        </Link>
        <Link href="/profile" className="bg-surface rounded-xl border border-border p-5 hover:bg-surface-elevated transition-colors">
          <h3 className="font-semibold mb-1">Manage Profile</h3>
          <p className="text-sm text-text-muted">Upload CV, edit details, add skills</p>
        </Link>
      </div>
    </>
  );
}

function StatCard({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="bg-surface rounded-xl border border-border p-4">
      <p className="text-xs text-text-muted mb-1">{label}</p>
      <p className={`text-xl font-bold ${accent ? "text-accent" : ""}`}>{value}</p>
    </div>
  );
}
