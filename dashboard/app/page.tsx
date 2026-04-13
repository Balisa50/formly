"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { api } from "./lib/api";
import WakeUp from "./components/WakeUp";

const ONBOARDING_STEPS = [
  {
    key: "cv",
    label: "Upload your CV",
    description: "Drop a PDF and Formly extracts your name, experience, education, and skills automatically.",
    href: "/profile",
  },
  {
    key: "personal",
    label: "Fill personal details",
    description: "Name, email, phone, address — the basics every form asks for.",
    href: "/profile",
  },
  {
    key: "education",
    label: "Add education",
    description: "Degrees, institutions, dates — filled once, reused everywhere.",
    href: "/profile",
  },
  {
    key: "experience",
    label: "Add work experience",
    description: "Jobs, roles, responsibilities — the meat of any application.",
    href: "/profile",
  },
];

export default function DashboardPage() {
  const [stats, setStats] = useState<any>(null);
  const [completeness, setCompleteness] = useState<number>(0);
  const [ready, setReady] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([api.getStats(), api.getCompleteness()]);
      setStats(s);
      setCompleteness(c.completeness);
      setReady(true);
      setLoaded(true);
    } catch {
      setReady(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleReady = useCallback(() => {
    setReady(true);
    loadData();
  }, [loadData]);

  if (!ready && !loaded) {
    return <WakeUp brandName="Formly" accentPart="ly" onReady={handleReady} />;
  }

  const hasApplications = stats && stats.total_applications > 0;

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

      {!hasApplications ? (
        /* Empty state — guide user to get started */
        <div className="space-y-6">
          <div className="bg-surface rounded-xl border border-border p-10 text-center">
            <div className="w-16 h-16 rounded-full bg-accent/10 flex items-center justify-center mx-auto mb-5">
              <svg className="w-8 h-8 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <h2 className="text-lg font-bold mb-2">Fill your first form</h2>
            <p className="text-text-muted text-sm max-w-md mx-auto mb-6">
              Build your profile, then paste any application URL. Formly reads every field and fills it using your saved details — jobs, scholarships, visas, university applications.
            </p>
            <div className="flex justify-center gap-3">
              <Link
                href="/profile"
                className="inline-flex bg-surface-elevated hover:bg-border text-text-primary text-sm px-5 py-2.5 rounded-lg border border-border transition-colors"
              >
                Build Profile
              </Link>
              <Link
                href="/fill"
                className="inline-flex bg-accent hover:bg-accent-hover text-white text-sm px-5 py-2.5 rounded-lg transition-colors font-medium"
              >
                Fill a Form
              </Link>
            </div>
          </div>

          {/* Onboarding steps */}
          {completeness < 50 && (
            <section>
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
        </div>
      ) : (
        /* Active dashboard with real data */
        <>
          <div className="grid grid-cols-3 gap-4 mb-8">
            <StatCard label="Applications" value={stats.total_applications} />
            <StatCard label="Submitted" value={stats.submitted} accent={stats.submitted > 0} />
            <StatCard label="Profile Fields" value={stats.profile_fields} />
          </div>

          <div className="grid md:grid-cols-2 gap-4">
            <Link href="/fill" className="bg-surface rounded-xl border border-border p-5 hover:bg-surface-elevated transition-colors group">
              <h3 className="font-semibold mb-1 group-hover:text-accent transition-colors">Fill a Form</h3>
              <p className="text-sm text-text-muted">
                Enter any application form URL — job, scholarship, university, or visa. Formly reads every field and fills it for you.
              </p>
            </Link>
            <Link href="/history" className="bg-surface rounded-xl border border-border p-5 hover:bg-surface-elevated transition-colors group">
              <h3 className="font-semibold mb-1 group-hover:text-accent transition-colors">View History</h3>
              <p className="text-sm text-text-muted">
                See every form you&apos;ve filled, what was submitted, and track your application progress.
              </p>
            </Link>
          </div>
        </>
      )}
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
