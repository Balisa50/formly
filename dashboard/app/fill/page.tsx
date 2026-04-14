"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "../lib/api";

type Phase = "idle" | "working" | "asking" | "filling" | "review" | "done";
type LogEntry = {
  type: "progress" | "filled" | "asking" | "essay" | "filling" | "error" | "screenshot" | "user" | "done" | "ready";
  message: string;
  data?: any;
};

type ReviewField = {
  label: string;
  selector: string;
  field_type: string;
  value: string;
  match_type: string;
  confidence: number;
  status: "filled" | "skipped";
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

  // Review state
  const [reviewFields, setReviewFields] = useState<ReviewField[]>([]);
  const [isRefilling, setIsRefilling] = useState(false);

  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  const addLog = useCallback((entry: LogEntry) => {
    setLog((prev) => [...prev, entry]);
  }, []);

  // ── Build review fields from matches and fill stats ──
  function buildReviewFields(matches: any[], stats: any): ReviewField[] {
    const fields: ReviewField[] = [];
    const seen = new Set<string>();

    // Add all matches (filled fields)
    for (const m of matches) {
      const key = m.selector || m.label;
      if (seen.has(key)) continue;
      seen.add(key);
      fields.push({
        label: m.label || m.selector || "Unknown field",
        selector: m.selector,
        field_type: m.field_type || "text",
        value: m.value || "",
        match_type: m.match_type || "unknown",
        confidence: m.confidence ?? 0,
        status: m.value ? "filled" : "skipped",
      });
    }

    // Add skipped fields from stats if available
    if (stats?.skipped_fields) {
      for (const s of stats.skipped_fields) {
        const key = s.selector || s.label;
        if (seen.has(key)) continue;
        seen.add(key);
        fields.push({
          label: s.label || s.selector || "Unknown field",
          selector: s.selector,
          field_type: s.field_type || "text",
          value: "",
          match_type: "skipped",
          confidence: 0,
          status: "skipped",
        });
      }
    }

    return fields;
  }

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
    setReviewFields([]);
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
      let latestStats: any = null;

      for (const event of events) {
        addLog({ type: event.type, message: event.message, data: event.data });

        if (event.type === "screenshot" && event.data?.screenshot) {
          setScreenshot(event.data.screenshot);
          setFillStats(event.data);
          latestStats = event.data;
        }
      }

      // Log the application
      const fieldsSnapshot: Record<string, string> = {};
      matches.forEach((m: any) => {
        if (m.value) fieldsSnapshot[m.label || m.selector] = m.value;
      });
      await api.logApplication(formUrl, pageContext, fieldsSnapshot);

      // Build review fields and go to review phase instead of done
      const fields = buildReviewFields(matches, latestStats);
      setReviewFields(fields);
      setPhase("review");
    } catch (err) {
      addLog({ type: "error", message: err instanceof Error ? err.message : "Fill failed" });
      setPhase("idle");
    }
  }

  // ── Re-fill with edited values ───────────────────
  async function handleRefill() {
    setIsRefilling(true);
    // Build updated matches from review fields
    const updatedMatches = reviewFields.map((f) => ({
      selector: f.selector,
      field_type: f.field_type,
      label: f.label,
      value: f.value,
      match_type: f.match_type,
      confidence: f.confidence,
    }));
    setFillMatches(updatedMatches);
    setScreenshot("");
    setFillStats(null);

    await doFill(agentUrl, updatedMatches, gapAnswers);
    setIsRefilling(false);
  }

  // ── Confirm review and proceed to done ───────────
  function handleConfirmReview() {
    setPhase("done");
  }

  // ── Update a review field value ──────────────────
  function updateReviewField(index: number, newValue: string) {
    setReviewFields((prev) =>
      prev.map((f, i) =>
        i === index
          ? { ...f, value: newValue, status: newValue ? "filled" : "skipped" }
          : f
      )
    );
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
      {phase !== "idle" && phase !== "review" && phase !== "done" && (
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

      {/* Review & Edit Section */}
      {(phase === "review" || phase === "done") && (
        <div className="mt-4 space-y-4">
          {/* Review table — shown in review phase, collapsed in done */}
          {phase === "review" && (
            <div className="bg-surface rounded-xl border border-border overflow-hidden">
              <div className="px-5 py-4 border-b border-border">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-accent/10 flex items-center justify-center shrink-0">
                    <svg className="w-5 h-5 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                    </svg>
                  </div>
                  <div>
                    <p className="font-bold text-lg">Review & Edit</p>
                    <p className="text-text-muted text-sm">
                      {reviewFields.filter((f) => f.status === "filled").length} filled,{" "}
                      {reviewFields.filter((f) => f.status === "skipped").length} skipped
                      — edit any value before re-filling.
                    </p>
                  </div>
                </div>
              </div>

              <div className="divide-y divide-white/5">
                {reviewFields.map((field, i) => (
                  <div key={field.selector || i} className="flex items-center gap-3 px-5 py-3">
                    {/* Status icon */}
                    <span className="shrink-0 text-base w-5 text-center" title={field.status === "filled" ? "Filled" : "Skipped"}>
                      {field.status === "filled" ? (
                        <svg className="w-4 h-4 text-green inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      ) : (
                        <svg className="w-4 h-4 text-amber-400 inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                      )}
                    </span>

                    {/* Field label */}
                    <span className="text-sm text-text-secondary w-40 shrink-0 truncate" title={field.label}>
                      {field.label}
                    </span>

                    {/* Editable value */}
                    <input
                      className="flex-1 bg-[#0a0a0a] border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder:text-white/30 focus:outline-none focus:border-accent/50 transition-colors"
                      value={field.value}
                      placeholder={field.status === "skipped" ? "Enter a value..." : ""}
                      onChange={(e) => updateReviewField(i, e.target.value)}
                    />
                  </div>
                ))}

                {reviewFields.length === 0 && (
                  <div className="px-5 py-8 text-center text-text-muted text-sm">
                    No fields were detected in this form.
                  </div>
                )}
              </div>

              {/* Review action buttons */}
              <div className="px-5 py-4 border-t border-border flex gap-3">
                <button
                  onClick={handleRefill}
                  disabled={isRefilling}
                  className="bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm px-5 py-2.5 rounded-lg transition-colors inline-flex items-center gap-2"
                >
                  {isRefilling ? (
                    <>
                      <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Re-filling...
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                      </svg>
                      Re-fill with Changes
                    </>
                  )}
                </button>

                <a
                  href={agentUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="border border-white/10 hover:border-white/20 text-white text-sm px-5 py-2.5 rounded-lg transition-colors inline-flex items-center gap-2"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                  </svg>
                  Open Form to Submit
                </a>

                <button
                  onClick={handleConfirmReview}
                  className="ml-auto text-text-muted hover:text-text-secondary text-sm px-4 py-2.5 transition-colors"
                >
                  Looks Good
                </button>
              </div>
            </div>
          )}

          {/* Done state summary (after confirming review) */}
          {phase === "done" && (
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
            </div>
          )}

          {/* Screenshot */}
          {screenshot && (
            <div className="bg-surface rounded-xl border border-border p-5">
              <p className="text-xs text-text-muted mb-2">Screenshot of the filled form:</p>
              <div className="border border-border rounded-lg overflow-hidden">
                <img src={`data:image/png;base64,${screenshot}`} alt="Filled form" className="w-full" />
              </div>
            </div>
          )}

          {/* Bottom actions — shown in done phase */}
          {phase === "done" && (
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
                setError(""); setReviewFields([]); setIsRefilling(false);
              }} className="text-text-muted text-sm px-4 py-2.5">Fill Another Form</button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
