"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import "./globals.css";
import AuthProvider, { useAuth } from "./components/AuthProvider";
import AuthGuard from "./components/AuthGuard";

const NAV = [
  { href: "/", label: "Dashboard", icon: "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0a1 1 0 01-1-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 01-1 1" },
  { href: "/profile", label: "Profile", icon: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" },
  { href: "/fill", label: "Fill Form", icon: "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" },
  { href: "/history", label: "History", icon: "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" },
];

function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, signOut } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
  }, [pathname, isMobile]);

  return (
    <div className="min-h-screen flex relative">
      {sidebarOpen && isMobile && (
        <div className="fixed inset-0 bg-black/50 z-30" onClick={() => setSidebarOpen(false)} />
      )}

      <aside
        className={`${
          isMobile
            ? `fixed inset-y-0 left-0 z-40 transform transition-transform duration-200 ${
                sidebarOpen ? "translate-x-0" : "-translate-x-full"
              }`
            : "relative"
        } w-56 shrink-0 bg-surface border-r border-border flex flex-col`}
      >
        <div className="p-5 border-b border-border flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold tracking-tight text-text-primary">
              Form<span className="text-accent">ly</span>
            </h1>
            <p className="text-xs text-text-muted mt-0.5">Fill once. Apply anywhere.</p>
          </div>
          {isMobile && (
            <button onClick={() => setSidebarOpen(false)} className="text-text-muted hover:text-text-primary">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {NAV.map((item) => {
            const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  active
                    ? "bg-accent/10 text-accent font-medium"
                    : "text-text-secondary hover:text-text-primary hover:bg-surface-elevated"
                }`}
              >
                <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d={item.icon} />
                </svg>
                {item.label}
              </Link>
            );
          })}
        </nav>

        {user && (
          <div className="p-4 border-t border-border">
            <p className="text-xs text-text-muted truncate mb-2">{user.email}</p>
            <button
              onClick={signOut}
              className="text-xs text-text-secondary hover:text-red transition-colors flex items-center gap-1.5"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
              Sign out
            </button>
          </div>
        )}
      </aside>

      <main className="flex-1 overflow-y-auto">
        {isMobile && (
          <div className="sticky top-0 z-20 bg-surface border-b border-border px-4 py-3 flex items-center gap-3">
            <button onClick={() => setSidebarOpen(true)} className="text-text-secondary hover:text-text-primary">
              <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <span className="font-bold text-text-primary">
              Form<span className="text-accent">ly</span>
            </span>
          </div>
        )}
        <div className="max-w-4xl mx-auto p-6">{children}</div>
      </main>
    </div>
  );
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <head>
        <title>Formly</title>
        <meta name="description" content="Fill once. Apply anywhere." />
      </head>
      <body className="min-h-screen bg-background text-text-primary">
        <AuthProvider>
          <AuthGuard>
            <AppShell>{children}</AppShell>
          </AuthGuard>
        </AuthProvider>
      </body>
    </html>
  );
}
