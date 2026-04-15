"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import "./globals.css";
import AuthProvider, { useAuth } from "./components/AuthProvider";
import AuthGuard from "./components/AuthGuard";

const NAV = [
  { href: "/profile", label: "Profile" },
  { href: "/fill", label: "Fill a Form" },
  { href: "/history", label: "History" },
];

function Navbar() {
  const pathname = usePathname();
  const { user, signOut } = useAuth();

  return (
    <nav className="sticky top-0 z-50 bg-surface/80 backdrop-blur-xl border-b border-border">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between">
        <div className="flex items-center gap-8">
          <Link href="/fill" className="text-lg font-bold tracking-tight text-text-primary shrink-0">
            Form<span className="text-accent">ly</span>
          </Link>

          <div className="flex items-center gap-1">
            {NAV.map((item) => {
              const active =
                pathname === item.href ||
                (item.href !== "/" && pathname.startsWith(item.href));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                    active
                      ? "bg-accent/10 text-accent font-medium"
                      : "text-text-secondary hover:text-text-primary hover:bg-surface-elevated"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>

        {user && (
          <div className="flex items-center gap-3">
            <span className="text-xs text-text-muted hidden sm:inline truncate max-w-[160px]">
              {user.email}
            </span>
            <button
              onClick={signOut}
              className="text-xs text-text-secondary hover:text-red transition-colors"
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </nav>
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
            <Navbar />
            <main className="max-w-4xl mx-auto px-4 sm:px-6 py-8">
              {children}
            </main>
          </AuthGuard>
        </AuthProvider>
      </body>
    </html>
  );
}
