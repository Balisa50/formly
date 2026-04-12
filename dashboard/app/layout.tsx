import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Formly",
  description: "Fill once. Apply anywhere.",
};

const NAV = [
  { href: "/", label: "Dashboard", icon: "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0a1 1 0 01-1-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 01-1 1" },
  { href: "/profile", label: "Profile", icon: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" },
  { href: "/fill", label: "Fill Form", icon: "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" },
  { href: "/history", label: "History", icon: "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" },
];

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-screen flex">
        <aside className="w-56 shrink-0 bg-surface border-r border-border flex flex-col">
          <div className="p-5 border-b border-border">
            <h1 className="text-lg font-bold tracking-tight text-text-primary">
              Form<span className="text-accent">ly</span>
            </h1>
            <p className="text-xs text-text-muted mt-0.5">Fill once. Apply anywhere.</p>
          </div>
          <nav className="flex-1 p-3 space-y-1">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-text-secondary hover:text-text-primary hover:bg-surface-elevated transition-colors"
              >
                <svg className="w-5 h-5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d={item.icon} />
                </svg>
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="p-4 border-t border-border">
            <p className="text-xs text-text-muted">v0.1.0</p>
          </div>
        </aside>
        <main className="flex-1 overflow-y-auto">
          <div className="max-w-4xl mx-auto p-6">{children}</div>
        </main>
      </body>
    </html>
  );
}
