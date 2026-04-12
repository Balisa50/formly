"use client";

import { useState } from "react";
import { useAuth } from "./AuthProvider";

interface Props {
  brandName: string;
  accentPart: string;
  tagline: string;
}

export default function AuthPage({ brandName, accentPart, tagline }: Props) {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [signupSuccess, setSignupSuccess] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    if (mode === "login") {
      const { error } = await signIn(email, password);
      if (error) setError(error);
    } else {
      const { error } = await signUp(email, password);
      if (error) {
        setError(error);
      } else {
        setSignupSuccess(true);
      }
    }

    setLoading(false);
  }

  const prefix = brandName.replace(accentPart, "");

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold tracking-tight text-text-primary">
            {prefix}<span className="text-accent">{accentPart}</span>
          </h1>
          <p className="text-sm text-text-muted mt-1">{tagline}</p>
        </div>

        {signupSuccess ? (
          <div className="bg-surface rounded-xl border border-border p-6 text-center">
            <div className="w-12 h-12 rounded-full bg-green/10 flex items-center justify-center mx-auto mb-3">
              <svg className="w-6 h-6 text-green" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="font-medium mb-2">Check your email</p>
            <p className="text-sm text-text-muted mb-4">
              We sent a confirmation link to <span className="text-accent">{email}</span>. Click it to activate your account.
            </p>
            <button
              onClick={() => { setSignupSuccess(false); setMode("login"); }}
              className="text-sm text-accent hover:text-accent-hover"
            >
              Back to login
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="bg-surface rounded-xl border border-border p-6 space-y-4">
            <div>
              <label className="block text-sm text-text-secondary mb-1">Email</label>
              <input
                type="email"
                required
                className="input"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            <div>
              <label className="block text-sm text-text-secondary mb-1">Password</label>
              <input
                type="password"
                required
                minLength={6}
                className="input"
                placeholder={mode === "signup" ? "At least 6 characters" : "Your password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>

            {error && (
              <div className="bg-red/10 text-red text-sm p-3 rounded-lg">{error}</div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-accent hover:bg-accent-hover disabled:opacity-50 text-white py-2.5 rounded-lg text-sm font-medium transition-colors"
            >
              {loading ? "..." : mode === "login" ? "Sign In" : "Create Account"}
            </button>

            <p className="text-center text-sm text-text-muted">
              {mode === "login" ? (
                <>
                  Don&apos;t have an account?{" "}
                  <button type="button" onClick={() => { setMode("signup"); setError(""); }} className="text-accent hover:text-accent-hover">
                    Sign up
                  </button>
                </>
              ) : (
                <>
                  Already have an account?{" "}
                  <button type="button" onClick={() => { setMode("login"); setError(""); }} className="text-accent hover:text-accent-hover">
                    Sign in
                  </button>
                </>
              )}
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
