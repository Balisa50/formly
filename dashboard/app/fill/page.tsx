"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "../lib/api";

type Phase = "idle" | "working" | "asking" | "filling" | "done";
type LogEntry = {
  type: "progress" | "filled" | "asking" | "essay" | "filling" | "error" | "screenshot" | "user" | "done";
  message: string;
  data?: any;
};

export default function FillFormPage() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [url, setUrl] = useState("");
  const [log, setLog] = useState<LogEntry[]>([]);
  const [error, setError] = useState("");

  // Agent state
  const [fillMatches, setFillMatches] = useState<any[]>([]);
  const [gapQuestions, setGapQuestions] = useState<any[]>([]);
  const [gapIndex, setGapIndex] = useState(0);
  const [gapAnswers, setGapAnswers] = useState<Record<string, string>>({});
  const [userInput, setUserInput] = useState("");
  const [essayDrafts, setEssayDrafts] = useState<any[]>([]);
  const [agentUrl, setAgentUrl] = useState("");
  const [pageContext, setPageContext] = useState("");

  // Result
  const [screenshot, setScreenshot] = useState("");
  const [fillStats, setFillStats] = useState<any>(null);

  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  const addLog = useCallback((entry: LogEntry) => {
    setLog((prev) => [...prev, entry]);
  }, []);

  // ── Start the agent ──────────────────────────────
  async function handleStart() {
    if (!url.trim()) return;
    setPhase("working");
    setError("");
    setLog([]);
    setFillMatches([]);
    setGapQuestions([]);
    setGapIndex(0);
    setGapAnswers({});
    setEssayDrafts([]);
    setScreenshot("");
    setFillStats(null);
    setAgentUrl(url);

    try {
      const { events } = await api.agentStart(url);

      // Process events one by one with delays for live feel
      for (const event of events) {
        addLog({ type: event.type, message: event.message, data: event.data });

        if (event.type === "ready") {
          const d = event.data;
          setFillMatches(d.fill_matches || []);
          setGapQuestions(d.gap_questions || []);
          setEssayDrafts(d.essay_drafts || []);
          setPageContext(d.page_context || "");

          if (d.gap_questions?.length > 0) {
            setPhase("asking");
          } else {
            // No gaps — go straight to filling
            await doFill(url, d.fill_matches, {});
          }
        }

        if (event.type === "error") {
          setError(event.message);
          setPhase("idle");
          return;
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Agent failed to start");
      setPhase("idle");
    }
  }

  // ── Handle gap answers ───────────────────────────
  async function handleGapAnswer() {
    if (!userInput.trim()) return;
    const answer = userInput.trim();
    setUserInput("");

    const question = gapQuestions[gapIndex];
    addLog({ type: "user", message: answer });
    addLog({ type: "progress", message: `Got it — saved "${answer}" for future forms.` });

    // Save to profile
    try {
      await api.saveGapAnswer(question.label, question.selector, question.field_type, answer);
    } catch {}

    // Store answer
    const newAnswers = { ...gapAnswers, [question.selector]: answer };
    setGapAnswers(newAnswers);

    // Also add to fill matches
    setFillMatches((prev) => [
      ...prev,
      {
        selector: question.selector,
        field_type: question.field_type,
        label: question.label,
        value: answer,
        match_type: "user_provided",
        confidence: 1.0,
      },
    ]);

    const nextIdx = gapIndex + 1;
    setGapIndex(nextIdx);

    if (nextIdx >= gapQuestions.length) {
      // All gaps answered — fill the form
      addLog({ type: "progress", message: "All questions answered. Filling the form now..." });
      const allMatches = [
        ...fillMatches,
        {
          selector: question.selector,
          field_type: question.field_type,
          label: question.label,
          value: answer,
          match_type: "user_provided",
          confidence: 1.0,
        },
      ];
      await doFill(agentUrl, allMatches, newAnswers);
    }
  }

  // ── Actually fill the form ───────────────────────
  async function doFill(formUrl: string, matches: any[], answers: Record<string, string>) {
    setPhase("filling");
    addLog({ type: "filling", message: "Agent is opening the form and filling fields..." });

    try {
      const { events } = await api.agentFill(formUrl, matches, answers);

      for (const event of events) {
        addLog({ type: event.type, message: event.message, data: event.data });

        if (event.type === "screenshot" && event.data?.screenshot) {
          setScreenshot(event.data.screenshot);
          setFillStats(event.data);
        }
      }

      // Log the application
      const fieldsSnapshot: Record<string, string> = {};
      matches.forEach((m: any) => {
        if (m.value) fieldsSnapshot[m.label || m.selector] = m.value;
      });
      await api.logApplication(formUrl, pageContext, fieldsSnapshot);

      setPhase("done");
    } catch (err) {
      addLog({ type: "error", message: err instanceof Error ? err.message : "Fill failed" });
      setPhase("idle");
    }
  }

  // ── Skip remaining questions and fill what we have ──
  function handleSkipAndFill() {
    addLog({ type: "progress", message: "Skipping remaining questions. Filling with what I have..." });
    doFill(agentUrl, fillMatches, gapAnswers);
  }

  return (
    <>
      <h1 className="text-2xl font-bold mb-1">Fill a Form</h1>
      <p className="text-sm text-text-muted mb-6">
        Paste any form URL. The agent reads it, fills from your profile, asks about the rest, and fills the form live.
      </p>

      {/* URL Input */}
      {phase === "idle" && (
        <div className="bg-surface rounded-xl border border-border p-5">
          <input className="input mb-3" placeholder="https://forms.example.com/apply" value={url}
            onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleStart()} />
          <button onClick={handleStart} disabled={!url.trim()}
            className="bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm px-5 py-2.5 rounded-lg transition-colors">
            Fill This Form
          </button>
          {error && <p className="text-sm text-red mt-3">{error}</p>}
        </div>
      )}

      {/* Live Activity Feed */}
      {phase !== "idle" && (
        <div className="bg-surface rounded-xl border border-border overflow-hidden">
          <div className="p-4 max-h-[500px] overflow-y-auto space-y-2">
            {log.map((entry, i) => (
              <div key={i} className={`flex items-start gap-2 ${entry.type === "user" ? "justify-end" : ""}`}>
                {entry.type === "user" ? (
                  <div className="bg-accent text-white rounded-xl px-4 py-2 text-sm max-w-[80%]">{entry.message}</div>
                ) : (
                  <>
                    <span className="mt-1 shrink-0">
                      {entry.type === "filled" && <span className="w-2 h-2 rounded-full bg-green inline-block" />}
                      {entry.type === "progress" && <span className="w-2 h-2 rounded-full bg-accent inline-block" />}
                      {entry.type === "filling" && <span className="w-2 h-2 rounded-full bg-amber-400 inline-block animate-pulse" />}
                      {entry.type === "asking" && <span className="w-2 h-2 rounded-full bg-blue-400 inline-block" />}
                      {entry.type === "essay" && <span className="w-2 h-2 rounded-full bg-purple-400 inline-block" />}
                      {entry.type === "error" && <span className="w-2 h-2 rounded-full bg-red inline-block" />}
                      {entry.type === "screenshot" && <span className="w-2 h-2 rounded-full bg-green inline-block" />}
                      {entry.type === "done" && <span className="w-2 h-2 rounded-full bg-green inline-block" />}
                      {entry.type === "ready" && <span className="w-2 h-2 rounded-full bg-green inline-block" />}
                    </span>
                    <span className={`text-sm ${
                      entry.type === "error" ? "text-red" :
                      entry.type === "filled" ? "text-green" :
                      entry.type === "filling" ? "text-amber-400" :
                      "text-text-secondary"
                    }`}>{entry.message}</span>
                  </>
                )}
              </div>
            ))}

            {phase === "working" && (
              <div className="flex items-center gap-2 text-sm text-accent">
                <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
                Working...
              </div>
            )}
            {phase === "filling" && (
              <div className="flex items-center gap-2 text-sm text-amber-400">
                <div className="w-4 h-4 border-2 border-amber-400/30 border-t-amber-400 rounded-full animate-spin" />
                Agent is typing into the form...
              </div>
            )}
            <div ref={logEndRef} />
          </div>

          {/* Gap question input */}
          {phase === "asking" && gapIndex < gapQuestions.length && (
            <div className="border-t border-border p-3">
              <div className="flex gap-2">
                <input className="input flex-1" placeholder="Type your answer..." value={userInput}
                  onChange={(e) => setUserInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleGapAnswer()} autoFocus />
                <button onClick={handleGapAnswer} className="bg-accent text-white text-sm px-4 py-2 rounded-lg">Send</button>
              </div>
              <button onClick={handleSkipAndFill} className="text-xs text-text-muted mt-2 hover:text-text-secondary">
                Skip remaining questions and fill what you have
              </button>
            </div>
          )}
        </div>
      )}

      {/* Screenshot + Result */}
      {phase === "done" && screenshot && (
        <div className="mt-4 space-y-4">
          <div className="bg-surface rounded-xl border border-border p-5">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-green/10 flex items-center justify-center shrink-0">
                <svg className="w-5 h-5 text-green" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <div>
                <p className="font-bold text-lg">Form Filled!</p>
                <p className="text-text-muted text-sm">
                  {fillStats?.filled || 0} fields filled{fillStats?.skipped > 0 ? `, ${fillStats.skipped} skipped` : ""}
                  {fillStats?.pages > 1 ? ` across ${fillStats.pages} pages` : ""}.
                </p>
              </div>
            </div>

            {fillStats?.errors?.length > 0 && (
              <div className="bg-amber-400/10 text-amber-400 text-xs p-3 rounded-lg mb-4">
                <p className="font-medium mb-1">Issues:</p>
                {fillStats.errors.map((e: string, i: number) => <p key={i}>- {e}</p>)}
              </div>
            )}

            <p className="text-xs text-text-muted mb-2">Screenshot of the filled form:</p>
            <div className="border border-border rounded-lg overflow-hidden">
              <img src={`data:image/png;base64,${screenshot}`} alt="Filled form" className="w-full" />
            </div>
          </div>

          <div className="flex gap-3">
            <a href={agentUrl} target="_blank" rel="noopener noreferrer"
              className="bg-accent hover:bg-accent-hover text-white text-sm px-5 py-2.5 rounded-lg inline-flex items-center gap-2">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
              </svg>
              Open Form to Review & Submit
            </a>
            <button onClick={() => {
              setPhase("idle"); setUrl(""); setLog([]); setFillMatches([]); setGapQuestions([]);
              setGapIndex(0); setGapAnswers({}); setEssayDrafts([]); setScreenshot(""); setFillStats(null);
              setError("");
            }} className="text-text-muted text-sm px-4 py-2.5">Fill Another Form</button>
          </div>
        </div>
      )}
    </>
  );
}
