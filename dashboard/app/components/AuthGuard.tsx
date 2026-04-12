"use client";

import { useAuth } from "./AuthProvider";
import AuthPage from "./AuthPage";

const hasSupabase = !!(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (!hasSupabase) return <>{children}</>;

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center">
          <h1 className="text-xl font-bold tracking-tight text-text-primary mb-4">
            Form<span className="text-accent">ly</span>
          </h1>
          <div className="w-8 h-8 border-3 border-border border-t-accent rounded-full animate-spin mx-auto" />
        </div>
      </div>
    );
  }

  if (!user) {
    return <AuthPage brandName="Formly" accentPart="ly" tagline="Fill once. Apply anywhere." />;
  }

  return <>{children}</>;
}
