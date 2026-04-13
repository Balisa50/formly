"use client";

import { useState, useRef, useEffect } from "react";
import { api } from "../lib/api";

type Step = "input" | "scanning" | "matching" | "autofill" | "gaps" | "essays" | "preview" | "done";
type ChatMsg = { role: "assistant" | "user"; content: string };

export default function FillFormPage() {
  const [step, setStep] = useState<Step>("input");
  const [url, setUrl] = useState("");
  const [fields, setFields] = useState<any[]>([]);
  const [pageContext, setPageContext] = useState("");
  const [matches, setMatches] = useState<any[]>([]);
  const [chat, setChat] = useState<ChatMsg[]>([]);
  const [gapQueue, setGapQueue] = useState<any[]>([]);
  const [gapIndex, setGapIndex] = useState(0);
  const [userInput, setUserInput] = useState("");
  const [essayDrafts, setEssayDrafts] = useState<Record<number, string>>({});
  const [error, setError] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat]);

  function addChat(role: "assistant" | "user", content: string) {
    setChat((prev) => [...prev, { role, content }]);
  }

  // Step 1: Scan the form
  async function handleScan() {
    if (!url.trim()) return;
    setStep("scanning");
    setError("");
    setChat([]);
    addChat("assistant", `Opening the form and reading all fields...`);

    try {
      const result = await api.scanForm(url);
      setFields(result.fields);
      setPageContext(result.page_context);
      addChat("assistant", `Found ${result.count} fields. Matching them to your profile...`);
      setStep("matching");
      await handleMatch(result.fields, result.page_context);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read this form. Check the URL and try again.");
      setStep("input");
    }
  }

  // Step 2: Match fields to profile
  async function handleMatch(scannedFields: any[], ctx: string) {
    try {
      const result = await api.matchFields(url, scannedFields, ctx);
      setMatches(result.matches);

      const autoFilled = result.auto_filled;
      const unknown = result.matches.filter((m: any) => m.match_type === "unknown" && !m.needs_essay && m.field_type !== "file");
      const essays = result.matches.filter((m: any) => m.needs_essay);
      const skipped = result.matches.filter((m: any) => m.match_type === "skipped" || m.field_type === "file");

      if (autoFilled > 0) {
        addChat("assistant", `Filled ${autoFilled} fields from your profile.`);
      }

      if (skipped.length > 0) {
        addChat("assistant", `${skipped.length} file upload field${skipped.length > 1 ? "s" : ""} detected — you'll need to upload ${skipped.length > 1 ? "those" : "that"} directly on the form.`);
      }

      if (unknown.length > 0) {
        addChat("assistant", `${unknown.length} fields I'm not sure about — let me try to figure them out...`);
        setStep("autofill");
        await handleAutoFill(result.matches, unknown, ctx, essays.length);
      } else if (essays.length > 0) {
        addChat("assistant", `${essays.length} fields need written responses — drafting those now...`);
        setStep("essays");
      } else {
        addChat("assistant", "All fields filled! Review everything below.");
        setStep("preview");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Matching failed");
      setStep("input");
    }
  }

  // Step 3: Auto-fill unknown fields using AI inference
  async function handleAutoFill(allMatches: any[], unknown: any[], ctx: string, essayCount: number) {
    try {
      const result = await api.autoFill(unknown, ctx);
      const filled = result.auto_filled || [];
      const remaining = result.still_unknown || [];

      // Update matches with auto-filled values
      if (filled.length > 0) {
        const filledMap = new Map(filled.map((f: any) => [f.selector, f]));
        setMatches((prev) =>
          prev.map((m) => {
            const fill = filledMap.get(m.selector);
            if (fill) return { ...m, value: fill.value, confidence: fill.confidence, match_type: "inferred" };
            return m;
          })
        );
        addChat("assistant", `Figured out ${filled.length} more fields on my own. ${filled.map((f: any) => f.reason).filter(Boolean).join(". ")}`);
      }

      if (remaining.length > 0) {
        addChat("assistant", `I still need your help with ${remaining.length} field${remaining.length > 1 ? "s" : ""}.`);
        setGapQueue(remaining);
        setGapIndex(0);
        setStep("gaps");
        await askNextGap(remaining, 0, ctx);
      } else if (essayCount > 0) {
        addChat("assistant", "Now drafting written responses...");
        setStep("essays");
      } else {
        addChat("assistant", "All fields filled! Review everything below.");
        setStep("preview");
      }
    } catch {
      // Fallback: go to gap filling for all unknown
      if (unknown.length > 0) {
        setGapQueue(unknown);
        setGapIndex(0);
        setStep("gaps");
        await askNextGap(unknown, 0, ctx);
      }
    }
  }

  // Step 4: Ask user about remaining gaps
  async function askNextGap(queue: any[], idx: number, ctx: string) {
    if (idx >= queue.length) {
      addChat("assistant", "Thanks! Let me check if any written responses are needed...");
      const essayFields = matches.filter((m) => m.needs_essay);
      if (essayFields.length > 0) {
        setStep("essays");
      } else {
        addChat("assistant", "All done! Review your answers below.");
        setStep("preview");
      }
      return;
    }

    const field = queue[idx];
    try {
      const { question } = await api.getGapQuestion(field.label, field.field_type, field.selector, ctx);
      addChat("assistant", question);
    } catch {
      // Fallback — never show selector
      const label = field.label?.startsWith("#") || field.label?.includes("select-")
        ? "a required field on this form"
        : field.label;
      addChat("assistant", `I need to know: what should I put for "${label}"?`);
    }
  }

  async function handleGapAnswer() {
    if (!userInput.trim()) return;
    const answer = userInput.trim();
    setUserInput("");
    addChat("user", answer);

    const field = gapQueue[gapIndex];

    // Save to profile permanently
    await api.saveGapAnswer(field.label, field.selector, field.field_type, answer);

    // Update match
    setMatches((prev) =>
      prev.map((m) =>
        m.selector === field.selector ? { ...m, value: answer, confidence: 1.0, match_type: "direct" } : m
      )
    );

    addChat("assistant", "Got it — saved so I won't ask again next time.");

    const nextIdx = gapIndex + 1;
    setGapIndex(nextIdx);
    await askNextGap(gapQueue, nextIdx, pageContext);
  }

  // Step 5: Essays
  async function generateEssays() {
    const essayFields = matches.filter((m) => m.needs_essay);
    const drafts: Record<number, string> = {};

    for (let i = 0; i < essayFields.length; i++) {
      const field = essayFields[i];
      const idx = matches.indexOf(field);
      try {
        const { essay } = await api.generateEssay(field.label, pageContext, field.max_length);
        drafts[idx] = essay;
      } catch {
        drafts[idx] = "";
      }
    }

    setEssayDrafts(drafts);
  }

  useEffect(() => {
    if (step === "essays" && Object.keys(essayDrafts).length === 0) {
      generateEssays();
    }
  }, [step]);

  return (
    <>
      <h1 className="text-2xl font-bold mb-1">Fill a Form</h1>
      <p className="text-sm text-text-muted mb-6">
        Paste any application form URL — Formly reads every field, matches your profile, and fills it automatically.
      </p>

      {/* URL Input */}
      {step === "input" && (
        <div className="bg-surface rounded-xl border border-border p-5">
          <input
            className="input mb-3"
            placeholder="https://forms.example.com/apply"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleScan()}
          />
          <button
            onClick={handleScan}
            disabled={!url.trim()}
            className="bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm px-5 py-2.5 rounded-lg transition-colors"
          >
            Fill This Form
          </button>
          {error && <p className="text-sm text-red mt-3">{error}</p>}
        </div>
      )}

      {/* Scanning spinner */}
      {step === "scanning" && (
        <div className="bg-surface rounded-xl border border-border p-8 text-center">
          <div className="w-8 h-8 border-3 border-border border-t-accent rounded-full animate-spin mx-auto mb-3" />
          <p className="text-text-muted text-sm">Reading form fields — this may take a moment...</p>
        </div>
      )}

      {/* Chat area */}
      {["gaps", "matching", "autofill"].includes(step) && (
        <div className="bg-surface rounded-xl border border-border">
          <div className="p-4 max-h-96 overflow-y-auto space-y-3">
            {chat.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[80%] rounded-xl px-4 py-2.5 text-sm ${
                    msg.role === "user"
                      ? "bg-accent text-white"
                      : "bg-surface-elevated text-text-secondary"
                  }`}
                >
                  {msg.content}
                </div>
              </div>
            ))}
            {step === "autofill" && (
              <div className="flex justify-start">
                <div className="bg-surface-elevated rounded-xl px-4 py-2.5 text-sm text-text-secondary flex items-center gap-2">
                  <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
                  Thinking...
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>
          {step === "gaps" && (
            <div className="border-t border-border p-3 flex gap-2">
              <input
                className="input flex-1"
                placeholder="Type your answer..."
                value={userInput}
                onChange={(e) => setUserInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleGapAnswer()}
                autoFocus
              />
              <button onClick={handleGapAnswer} className="bg-accent text-white text-sm px-4 py-2 rounded-lg">
                Send
              </button>
            </div>
          )}
        </div>
      )}

      {/* Essays */}
      {step === "essays" && (
        <div className="bg-surface rounded-xl border border-border p-5 space-y-4">
          <h2 className="font-semibold">Written Responses</h2>
          <p className="text-sm text-text-muted">Drafted from your profile. Edit anything before continuing.</p>
          {matches
            .filter((m) => m.needs_essay)
            .map((field) => {
              const idx = matches.indexOf(field);
              return (
                <div key={idx}>
                  <p className="text-sm font-medium mb-2">{field.label}</p>
                  {essayDrafts[idx] === undefined ? (
                    <div className="flex items-center gap-2 text-sm text-accent">
                      <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
                      Writing a draft...
                    </div>
                  ) : (
                    <textarea
                      className="input"
                      rows={6}
                      value={essayDrafts[idx]}
                      onChange={(e) => {
                        setEssayDrafts({ ...essayDrafts, [idx]: e.target.value });
                        setMatches((prev) =>
                          prev.map((m, i) => (i === idx ? { ...m, value: e.target.value, needs_essay: false } : m))
                        );
                      }}
                    />
                  )}
                </div>
              );
            })}
          <button
            onClick={() => {
              Object.entries(essayDrafts).forEach(([idx, text]) => {
                setMatches((prev) =>
                  prev.map((m, i) => (i === parseInt(idx) ? { ...m, value: text, needs_essay: false } : m))
                );
              });
              setStep("preview");
            }}
            className="bg-accent hover:bg-accent-hover text-white text-sm px-4 py-2 rounded-lg"
          >
            Continue to Preview
          </button>
        </div>
      )}

      {/* Preview */}
      {step === "preview" && (
        <div className="space-y-4">
          <div className="bg-surface rounded-xl border border-border p-5">
            <h2 className="font-semibold mb-2">Review All Answers</h2>
            <p className="text-xs text-text-muted mb-4">Edit anything before submitting. Green = high confidence. Yellow = moderate. Red = low.</p>

            <div className="space-y-3">
              {matches
                .filter((m) => m.value)
                .map((m, i) => {
                  const dotColor = m.confidence >= 0.8 ? "bg-green" : m.confidence >= 0.5 ? "bg-amber-400" : "bg-red";
                  // Clean label for display — never show selectors
                  const displayLabel = m.label?.startsWith("#") || m.label?.includes("select-") || m.label?.includes("input[")
                    ? (m.note || m.profile_key || "Form field")
                    : m.label;
                  return (
                    <div key={i} className="flex gap-3 items-start">
                      <span className={`w-2 h-2 rounded-full mt-2.5 shrink-0 ${dotColor}`} />
                      <div className="flex-1">
                        <p className="text-xs text-text-muted mb-1">{displayLabel}</p>
                        {m.value.length > 100 ? (
                          <textarea
                            className="input"
                            rows={3}
                            value={m.value}
                            onChange={(e) =>
                              setMatches((prev) => prev.map((mm, j) => (j === matches.indexOf(m) ? { ...mm, value: e.target.value } : mm)))
                            }
                          />
                        ) : (
                          <input
                            className="input"
                            value={m.value}
                            onChange={(e) =>
                              setMatches((prev) => prev.map((mm, j) => (j === matches.indexOf(m) ? { ...mm, value: e.target.value } : mm)))
                            }
                          />
                        )}
                      </div>
                    </div>
                  );
                })}
            </div>
          </div>

          <div className="flex gap-3">
            <button
              onClick={async () => {
                const fieldsSnapshot: Record<string, string> = {};
                matches.forEach((m) => { if (m.value) fieldsSnapshot[m.label] = m.value; });
                await api.logApplication(url, pageContext, fieldsSnapshot);
                setStep("done");
              }}
              className="bg-accent hover:bg-accent-hover text-white text-sm px-6 py-2.5 rounded-lg"
            >
              Approve & Log Application
            </button>
            <button onClick={() => setStep("input")} className="text-text-muted text-sm px-4 py-2.5">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Done */}
      {step === "done" && (
        <div className="bg-surface rounded-xl border border-border p-10 text-center">
          <div className="w-14 h-14 rounded-full bg-green/10 flex items-center justify-center mx-auto mb-4">
            <svg className="w-7 h-7 text-green" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <p className="font-bold text-lg mb-2">Application Complete!</p>
          <p className="text-text-muted text-sm mb-5">All answers saved. Check History for your records.</p>
          <button
            onClick={() => {
              setStep("input");
              setUrl("");
              setFields([]);
              setMatches([]);
              setChat([]);
              setGapQueue([]);
              setGapIndex(0);
              setEssayDrafts({});
              setError("");
            }}
            className="bg-accent hover:bg-accent-hover text-white text-sm px-5 py-2.5 rounded-lg"
          >
            Fill Another Form
          </button>
        </div>
      )}
    </>
  );
}
