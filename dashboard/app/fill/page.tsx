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
  status: "filled" | "skipped" | "verified" | "error" | "needs_user";
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

  // Review
  const [reviewFields, setReviewFields] = useState<ReviewField[]>([]);
  const [isRefilling, setIsRefilling] = useState(false);

  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  const addLog = useCallback((entry: LogEntry) => {
    setLog((prev) => [...prev, entry]);
  }, []);

  function buildReviewFields(matches: any[], stats: any): ReviewField[] {
    const fields: ReviewField[] = [];
    const seen = new Set<string>();
    const fieldResults: any[] = stats?.field_results || [];

    if (fieldResults.length > 0) {
      for (const fr of fieldResults) {
        const key = fr.selector || fr.label;
        if (seen.has(key)) continue;
        seen.add(key);
        fields.push({
          label: fr.label || fr.selector || "Unknown field",
          selector: fr.selector || "",
          field_type: fr.field_type || "text",
          value: fr.value || "",
          match_type: fr.status || "unknown",
          confidence: fr.status === "verified" ? 1.0 : fr.status === "filled" ? 0.7 : 0,
          status: (fr.status as ReviewField["status"]) || "skipped",
        });
      }
    }

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
        status: !m.value || m.match_type === "skipped" ? "skipped" : "filled",
      });
    }

    return fields;
  }

  // ---- Start Agent ----
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

      for (const event of events) {
        if (event.type === "asking") continue;
        addLog({ type: event.type, message: event.message, data: event.data });

        if (event.type === "ready") {
          const d = event.data;
          setFillMatches(d.fill_matches || []);
          setEssayDrafts(d.essay_drafts || []);
          setPageContext(d.page_context || "");

          const rawQuestions = d.gap_questions || [];
          const deduped: any[] = [];
          const seenLabels = new Set<string>();
          for (const q of rawQuestions) {
            const key = (q.label || q.question || "").toLowerCase().replace(/[^a-z]/g, "");
            if (seenLabels.has(key)) continue;
            seenLabels.add(key);
            deduped.push(q);
          }
          setGapQuestions(deduped);

          if (d.gap_questions?.length > 0) {
            setPhase("asking");
          } else {
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

  // ---- Gap Answers ----
  async function handleGapAnswer() {
    if (!userInput.trim()) return;
    const answer = userInput.trim();
    setUserInput("");

    const question = gapQuestions[gapIndex];
    addLog({ type: "user", message: answer });
    addLog({ type: "progress", message: `Got it -- saved for future forms.` });

    try {
      await api.saveGapAnswer(question.label, question.selector, question.field_type, answer);
    } catch {}

    const newAnswers = { ...gapAnswers, [question.selector]: answer };
    setGapAnswers(newAnswers);

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

  // ---- Fill Form ----
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

      const fieldsSnapshot: Record<string, string> = {};
      matches.forEach((m: any) => {
        if (m.value) fieldsSnapshot[m.label || m.selector] = m.value;
      });
      await api.logApplication(formUrl, pageContext, fieldsSnapshot);

      const fields = buildReviewFields(matches, latestStats);
      setReviewFields(fields);
      setPhase("review");
    } catch (err) {
      addLog({ type: "error", message: err instanceof Error ? err.message : "Fill failed" });
      setPhase("idle");
    }
  }

  // ---- Re-fill ----
  async function handleRefill() {
    setIsRefilling(true);
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

  function handleConfirmReview() {
    setPhase("done");
  }

  function updateReviewField(index: number, newValue: string) {
    setReviewFields((prev) =>
      prev.map((f, i) => {
        if (i !== index) return f;
        const hasValue = !!newValue.trim();
        // When user provides a value for a needs_user field, promote match_type
        // so the backend doesn't skip it again on refill
        const nextMatchType =
          f.match_type === "needs_user" && hasValue ? "selection" : f.match_type;
        return {
          ...f,
          value: newValue,
          match_type: nextMatchType,
          status: hasValue ? (f.status === "verified" ? "verified" : "filled") : "skipped",
        };
      })
    );
  }

  function handleSkipAndFill() {
    addLog({ type: "progress", message: "Skipping remaining questions. Filling with what I have..." });
    doFill(agentUrl, fillMatches, gapAnswers);
  }

  function resetAll() {
    setPhase("idle");
    setUrl("");
    setLog([]);
    setFillMatches([]);
    setGapQuestions([]);
    setGapIndex(0);
    setGapAnswers({});
    setEssayDrafts([]);
    setScreenshot("");
    setFillStats(null);
    setError("");
    setReviewFields([]);
    setIsRefilling(false);
  }

  // ---- IDLE: Centered URL Input ----
  if (phase === "idle") {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh]">
        <h1 className="text-3xl font-bold mb-2">Fill a Form</h1>
        <p className="text-text-muted text-sm mb-8 text-center max-w-md">
          Paste any application form URL. The agent reads every field, matches your profile, and fills it for you.
        </p>
        <div className="w-full max-w-lg">
          <div className="flex gap-2">
            <input
              className="input flex-1 !py-3 !px-4 !text-base"
              placeholder="https://forms.example.com/apply"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleStart()}
              autoFocus
            />
            <button
              onClick={handleStart}
              disabled={!url.trim()}
              className="bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm px-6 py-3 rounded-lg transition-colors font-medium shrink-0"
            >
              Start Filling
            </button>
          </div>
          {error && <p className="text-sm text-red mt-3 text-center">{error}</p>}
        </div>
      </div>
    );
  }

  // ---- WORKING / ASKING / FILLING: Activity Feed ----
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-6">
      {/* Left: Status / Review */}
      <div>
        {/* Header */}
        <div className="flex items-center gap-3 mb-4">
          {phase !== "done" && phase !== "review" && (
            <div className="w-3 h-3 rounded-full bg-accent animate-pulse" />
          )}
          <h1 className="text-xl font-bold">
            {phase === "working" && "Reading the form..."}
            {phase === "asking" && "Quick questions"}
            {phase === "filling" && "Filling fields..."}
            {phase === "review" && "Review & Edit"}
            {phase === "done" && "Form Filled!"}
          </h1>
        </div>

        {/* Review Table */}
        {(phase === "review" || phase === "done") && (
          <div className="space-y-4">
            {phase === "review" && (
              <div className="bg-surface rounded-xl border border-border overflow-hidden">
                <div className="px-5 py-4 border-b border-border">
                  <p className="text-sm text-text-muted">
                    {reviewFields.filter((f) => f.status === "verified" || f.status === "filled").length} filled,{" "}
                    {reviewFields.filter((f) => f.status === "skipped").length} skipped.
                    Edit any value then re-fill or confirm.
                  </p>
                </div>

                <div className="divide-y divide-border">
                  {reviewFields.map((field, i) => (
                    <div key={field.selector || i} className="flex items-center gap-3 px-5 py-3">
                      <span className="shrink-0 w-5 text-center">
                        {(field.status === "verified" || field.status === "filled") && (
                          <svg className="w-4 h-4 text-green inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                        )}
                        {field.status === "error" && (
                          <svg className="w-4 h-4 text-amber-400 inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M10.29 3.86l-8.4 14.31A1 1 0 002.72 20h18.56a1 1 0 00.85-1.47l-8.4-14.31a1.02 1.02 0 00-1.74 0z" />
                          </svg>
                        )}
                        {field.status === "skipped" && (
                          <svg className="w-4 h-4 text-red inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        )}
                        {field.status === "needs_user" && (
                          <svg className="w-4 h-4 text-blue-400 inline-block" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                          </svg>
                        )}
                      </span>
                      <span className="text-sm text-text-secondary w-36 shrink-0 truncate" title={field.label}>
                        {field.label}
                      </span>
                      {(() => {
                        // Detect the needs_user / "Options: ..." case
                        const optionsMatch = /^Options:\s*(.+)$/i.exec(field.value || "");
                        if (optionsMatch && field.status === "needs_user") {
                          const opts = optionsMatch[1].split(",").map((s) => s.trim()).filter(Boolean);
                          return (
                            <div className="flex-1">
                              <select
                                className="w-full bg-background border border-accent/40 rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent transition-colors cursor-pointer"
                                defaultValue=""
                                onChange={(e) => {
                                  if (e.target.value) updateReviewField(i, e.target.value);
                                }}
                              >
                                <option value="" disabled>
                                  Pick an option...
                                </option>
                                {opts.map((o) => (
                                  <option key={o} value={o}>
                                    {o}
                                  </option>
                                ))}
                              </select>
                              <p className="text-xs text-accent mt-1">
                                Agent needs you to pick one of these — then click Re-fill.
                              </p>
                            </div>
                          );
                        }
                        return (
                          <input
                            className="flex-1 bg-background border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50 transition-colors"
                            value={field.value}
                            placeholder={field.status === "skipped" ? "Enter a value..." : ""}
                            onChange={(e) => updateReviewField(i, e.target.value)}
                          />
                        );
                      })()}
                    </div>
                  ))}
                  {reviewFields.length === 0 && (
                    <div className="px-5 py-8 text-center text-text-muted text-sm">
                      No fields were detected in this form.
                    </div>
                  )}
                </div>

                <div className="px-5 py-4 border-t border-border flex gap-3">
                  <button
                    onClick={handleRefill}
                    disabled={isRefilling}
                    className="border border-border hover:border-text-muted disabled:opacity-50 text-text-primary text-sm px-5 py-2.5 rounded-lg transition-colors inline-flex items-center gap-2"
                  >
                    {isRefilling ? (
                      <>
                        <div className="w-4 h-4 border-2 border-text-muted border-t-text-primary rounded-full animate-spin" />
                        Re-filling...
                      </>
                    ) : (
                      "Re-fill with Changes"
                    )}
                  </button>
                  <button
                    onClick={handleConfirmReview}
                    className="ml-auto bg-green/90 hover:bg-green text-white text-sm px-5 py-2.5 rounded-lg transition-colors font-medium"
                  >
                    Submit Form
                  </button>
                </div>
              </div>
            )}

            {/* Done state */}
            {phase === "done" && (
              <div className="bg-surface rounded-xl border border-border p-6 text-center">
                <div className="w-14 h-14 rounded-full bg-green/10 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-7 h-7 text-green" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <p className="font-bold text-xl mb-1">Form Filled Successfully</p>
                <p className="text-text-muted text-sm mb-6">
                  {fillStats?.filled || 0} fields filled
                  {fillStats?.skipped > 0 ? `, ${fillStats.skipped} skipped` : ""}
                  {fillStats?.pages > 1 ? ` across ${fillStats.pages} pages` : ""}.
                </p>

                {fillStats?.errors?.length > 0 && (
                  <div className="bg-amber-400/10 text-amber-400 text-xs p-3 rounded-lg mb-4 text-left">
                    <p className="font-medium mb-1">Issues:</p>
                    {fillStats.errors.map((e: string, i: number) => <p key={i}>- {e}</p>)}
                  </div>
                )}

                <button
                  onClick={resetAll}
                  className="bg-accent hover:bg-accent-hover text-white text-sm px-6 py-2.5 rounded-lg transition-colors"
                >
                  Fill Another Form
                </button>
              </div>
            )}

            {/* Screenshot */}
            {screenshot && (
              <div className="bg-surface rounded-xl border border-border p-4">
                <p className="text-xs text-text-muted mb-2">Screenshot of the filled form:</p>
                <div className="border border-border rounded-lg overflow-hidden">
                  <img src={`data:image/png;base64,${screenshot}`} alt="Filled form" className="w-full" />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Right: Live Activity Feed */}
      <div className="bg-surface rounded-xl border border-border overflow-hidden h-fit lg:sticky lg:top-20">
        <div className="px-4 py-3 border-b border-border">
          <p className="text-xs font-medium text-text-secondary uppercase tracking-wider">Activity</p>
        </div>

        <div className="p-4 max-h-[500px] overflow-y-auto space-y-2">
          {log.map((entry, i) => (
            <div key={i} className={`flex items-start gap-2 ${entry.type === "user" ? "justify-end" : ""}`}>
              {entry.type === "user" ? (
                <div className="bg-accent text-white rounded-xl px-3 py-2 text-xs max-w-[85%]">{entry.message}</div>
              ) : (
                <>
                  <span className="mt-1.5 shrink-0">
                    <span className={`w-1.5 h-1.5 rounded-full inline-block ${
                      entry.type === "filled" || entry.type === "done" || entry.type === "ready" || entry.type === "screenshot"
                        ? "bg-green"
                        : entry.type === "error"
                        ? "bg-red"
                        : entry.type === "filling"
                        ? "bg-amber-400 animate-pulse"
                        : "bg-accent"
                    }`} />
                  </span>
                  <span className={`text-xs ${
                    entry.type === "error" ? "text-red" :
                    entry.type === "filled" ? "text-green" :
                    entry.type === "filling" ? "text-amber-400" :
                    "text-text-secondary"
                  }`}>{entry.message}</span>
                </>
              )}
            </div>
          ))}

          {(phase === "working" || phase === "filling") && (
            <div className="flex items-center gap-2 text-xs text-accent">
              <div className="w-3 h-3 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
              {phase === "working" ? "Working..." : "Filling..."}
            </div>
          )}
          <div ref={logEndRef} />
        </div>

        {/* Gap Questions */}
        {phase === "asking" && gapIndex < gapQuestions.length && (
          <div className="border-t border-border p-3 space-y-3">
            <div className="bg-blue-500/10 rounded-lg px-3 py-2">
              <p className="text-xs text-blue-400 font-medium">
                {gapQuestions[gapIndex].question || `What is your ${gapQuestions[gapIndex].label}?`}
              </p>
            </div>

            {gapQuestions[gapIndex].field_type?.includes("file") ? (
              <div className="space-y-2">
                <label className="flex flex-col items-center justify-center w-full h-20 border-2 border-dashed border-border rounded-xl cursor-pointer hover:border-accent/50 hover:bg-accent/5 transition-colors">
                  <svg className="w-6 h-6 text-text-muted mb-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                  </svg>
                  <span className="text-xs text-text-muted">Click to upload</span>
                  <input type="file" className="hidden"
                    accept={gapQuestions[gapIndex].field_type?.includes("photo") ? "image/*" : "*"}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      addLog({ type: "progress", message: `Uploaded: ${file.name}` });
                      const formData = new FormData();
                      formData.append("file", file);
                      const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
                      fetch(`${BASE}/api/profile/photo`, { method: "POST", body: formData }).catch(() => {});
                      const newAnswers = { ...gapAnswers, [gapQuestions[gapIndex].selector]: file.name };
                      setGapAnswers(newAnswers);
                      const nextIdx = gapIndex + 1;
                      setGapIndex(nextIdx);
                      if (nextIdx >= gapQuestions.length) {
                        addLog({ type: "progress", message: "All questions answered. Filling the form now..." });
                        doFill(agentUrl, [...fillMatches], newAnswers);
                      }
                    }}
                  />
                </label>
                <button onClick={() => {
                  const nextIdx = gapIndex + 1;
                  setGapIndex(nextIdx);
                  addLog({ type: "progress", message: `Skipped ${gapQuestions[gapIndex].label}.` });
                  if (nextIdx >= gapQuestions.length) {
                    doFill(agentUrl, [...fillMatches], gapAnswers);
                  }
                }} className="text-xs text-text-muted hover:text-text-secondary">
                  Skip this upload
                </button>
              </div>
            ) : (
              <div className="flex gap-2">
                <input
                  className="input flex-1 !text-xs"
                  placeholder="Type your answer..."
                  value={userInput}
                  onChange={(e) => setUserInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleGapAnswer()}
                  autoFocus
                />
                <button onClick={handleGapAnswer} className="bg-accent text-white text-xs px-3 py-1.5 rounded-lg">
                  Send
                </button>
              </div>
            )}
            <button onClick={handleSkipAndFill} className="text-xs text-text-muted hover:text-text-secondary block">
              Skip remaining and fill
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
