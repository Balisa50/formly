"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "./lib/api";

const ONBOARDING_STEPS = [
  {
    key: "cv",
    label: "Upload your CV",
    description: "Drop a PDF and Formly extracts your name, experience, education, and skills automatically.",
    href: "/profile",
    icon: "M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12",
  },
  {
    key: "personal",
    label: "Fill personal details",
    description: "Name, email, phone, address — the basics every form asks for.",
    href: "/profile",
    icon: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z",
  },
  {
    key: "education",
    label: "Add education",
    description: "Degrees, institutions, dates — filled once, reused everywhere.",
    href: "/profile",
    icon: "M12 14l9-5-9-5-9 5 9 5zm0 0l6.16-3.422a12.083 12.083 0 01.665 6.479A11.952 11.952 0 0012 20.055a11.952 11.952 0 00-6.824-2.998 12.078 12.078 0 01.665-6.479L12 14z",
  },
  {
    key: "experience",
    label: "Add work experience",
    description: "Jobs, roles, responsibilities — the meat of any application.",
    href: "/profile",
    icon: "M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z",
  },
];

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
        <h1 className="text-2xl font-bold mb-2">Backend Offline</h1>
        <p className="text-text-muted text-sm">
          The Formly server isn&apos;t responding. Please wait a moment and refresh — free instances take up to 50 seconds to wake up.
        </p>
      </div>
    );
  }

  const showOnboarding = completeness < 50;

  return (
    <>
      <h1 className="text-2xl font-bold mb-6">Dashboard</h1>

      {/* Profile completeness */}
      <div className="bg-surface rounded-xl border border-border p-5 mb-6">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-text-secondary">Profile Completeness</span>
          <span className="text-sm font-bold">{completeness}%</span>
        </div>
        <div className="w-full h-2.5 bg-background rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              completeness < 30 ? "bg-red" : completeness < 70 ? "bg-amber-400" : "bg-green"
            }`}
            style={{ width: `${completeness}%` }}
          />
        </div>
      </div>

      {/* Onboarding flow when profile is low */}
      {showOnboarding && (
        <section className="mb-8">
          <h2 className="font-semibold mb-1">Get started</h2>
          <p className="text-sm text-text-muted mb-4">Complete these steps so Formly can auto-fill forms for you.</p>
          <div className="grid gap-3">
            {ONBOARDING_STEPS.map((step, i) => (
              <Link
                key={step.key}
                href={step.href}
                className="bg-surface rounded-xl border border-border p-4 flex items-start gap-4 hover:bg-surface-elevated transition-colors group"
              >
                <div className="w-8 h-8 rounded-full bg-accent/10 text-accent flex items-center justify-center shrink-0 text-sm font-bold">
                  {i + 1}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium group-hover:text-accent transition-colors">{step.label}</p>
                  <p className="text-xs text-text-muted mt-0.5">{step.description}</p>
                </div>
                <svg className="w-5 h-5 text-text-muted shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
              </Link>
            ))}
          </div>
        </section>
      )}

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
        <Link href="/fill" className="bg-surface rounded-xl border border-border p-5 hover:bg-surface-elevated transition-colors group">
          <h3 className="font-semibold mb-1 group-hover:text-accent transition-colors">Fill a Form</h3>
          <p className="text-sm text-text-muted">
            Enter any application form URL — job, scholarship, university, or visa. Formly reads every field and fills it for you.
          </p>
        </Link>
        <Link href="/profile" className="bg-surface rounded-xl border border-border p-5 hover:bg-surface-elevated transition-colors group">
          <h3 className="font-semibold mb-1 group-hover:text-accent transition-colors">Manage Profile</h3>
          <p className="text-sm text-text-muted">
            Upload your CV, edit personal details, add work history and skills. The more you add, the smarter Formly gets.
          </p>
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
