"use client";

import { useState, useRef, useEffect } from "react";
import { api } from "../lib/api";

type Step = "input" | "scanning" | "matching" | "gaps" | "essays" | "preview" | "done";
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
  const [currentQuestion, setCurrentQuestion] = useState("");
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

  // Step 1: Scan
  async function handleScan() {
    if (!url.trim()) return;
    setStep("scanning");
    setError("");
    addChat("assistant", `Scanning form at ${url}...`);

    try {
      const result = await api.scanForm(url);
      setFields(result.fields);
      setPageContext(result.page_context);
      addChat("assistant", `Found **${result.count} fields**. Matching to your profile...`);
      setStep("matching");
      await handleMatch(result.fields, result.page_context);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan failed");
      setStep("input");
    }
  }

  // Step 2: Match
  async function handleMatch(scannedFields: any[], ctx: string) {
    try {
      const result = await api.matchFields(url, scannedFields, ctx);
      setMatches(result.matches);

      let msg = `**Matched ${result.auto_filled} fields** from your profile.`;
      if (result.needs_input > 0) msg += ` ${result.needs_input} missing — I'll ask you.`;
      if (result.needs_essay > 0) msg += ` ${result.needs_essay} need written responses.`;
      addChat("assistant", msg);

      // Find unmatched
      const unmatched = result.matches.filter((m: any) => m.match_type === "unknown" && !m.needs_essay);
      setGapQueue(unmatched);

      if (unmatched.length > 0) {
        setStep("gaps");
        await askNextGap(unmatched, 0, ctx);
      } else if (result.needs_essay > 0) {
        setStep("essays");
      } else {
        setStep("preview");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Matching failed");
      setStep("input");
    }
  }

  // Step 3: Gap filling
  async function askNextGap(queue: any[], idx: number, ctx: string) {
    if (idx >= queue.length) {
      const essayFields = matches.filter((m) => m.needs_essay);
      if (essayFields.length > 0) {
        setStep("essays");
      } else {
        setStep("preview");
      }
      return;
    }

    const field = queue[idx];
    try {
      const { question } = await api.getGapQuestion(field.label, field.field_type, field.selector, ctx);
      setCurrentQuestion(question);
      addChat("assistant", question);
    } catch {
      addChat("assistant", `What should I put for "${field.label}"?`);
      setCurrentQuestion(`What should I put for "${field.label}"?`);
    }
  }

  async function handleGapAnswer() {
    if (!userInput.trim()) return;
    const answer = userInput.trim();
    setUserInput("");
    addChat("user", answer);

    const field = gapQueue[gapIndex];

    // Save to profile
    await api.saveGapAnswer(field.label, field.selector, field.field_type, answer);

    // Update match
    setMatches((prev) =>
      prev.map((m) =>
        m.selector === field.selector ? { ...m, value: answer, confidence: 1.0, match_type: "direct" } : m
      )
    );

    addChat("assistant", "Got it! Saved to your profile — I'll remember this for future forms.");

    const nextIdx = gapIndex + 1;
    setGapIndex(nextIdx);
    await askNextGap(gapQueue, nextIdx, pageContext);
  }

  // Step 4: Essays
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
      <p className="text-sm text-text-muted mb-6">Paste any form URL and Formly handles the rest.</p>

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
            className="bg-accent hover:bg-accent-hover text-white text-sm px-4 py-2 rounded-lg transition-colors"
          >
            Scan Form
          </button>
          {error && <p className="text-sm text-red mt-3">{error}</p>}
        </div>
      )}

      {/* Scanning spinner */}
      {step === "scanning" && (
        <div className="bg-surface rounded-xl border border-border p-8 text-center">
          <p className="text-text-muted animate-pulse">Scanning form fields...</p>
        </div>
      )}

      {/* Chat area (gaps) */}
      {(step === "gaps" || step === "matching") && (
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
            <div ref={chatEndRef} />
          </div>
          {step === "gaps" && (
            <div className="border-t border-border p-3 flex gap-2">
              <input
                className="input flex-1"
                placeholder="Your answer..."
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
          {matches
            .filter((m) => m.needs_essay)
            .map((field) => {
              const idx = matches.indexOf(field);
              return (
                <div key={idx}>
                  <p className="text-sm font-medium mb-2">{field.label}</p>
                  {essayDrafts[idx] === undefined ? (
                    <p className="text-text-muted text-sm animate-pulse">Generating...</p>
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
              // Apply essay values to matches
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
            <h2 className="font-semibold mb-4">Review All Answers</h2>
            <p className="text-xs text-text-muted mb-4">Edit anything before submitting. Nothing is sent until you approve.</p>

            <div className="space-y-3">
              {matches
                .filter((m) => m.value)
                .map((m, i) => {
                  const color = m.confidence >= 0.8 ? "text-green" : m.confidence >= 0.5 ? "text-amber" : "text-red";
                  return (
                    <div key={i} className="flex gap-3 items-start">
                      <span className={`text-xs mt-2 ${color}`}>
                        {m.confidence >= 0.8 ? "●" : m.confidence >= 0.5 ? "●" : "●"}
                      </span>
                      <div className="flex-1">
                        <p className="text-xs text-text-muted mb-1">{m.label}</p>
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
                addChat("assistant", "Application logged! In a full setup, Playwright would now fill and submit the form.");
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
        <div className="bg-surface rounded-xl border border-border p-8 text-center">
          <p className="text-green text-lg font-bold mb-2">Application Complete!</p>
          <p className="text-text-muted text-sm mb-4">Check the History page for your records.</p>
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
            className="text-accent hover:text-accent-hover text-sm"
          >
            Fill Another Form
          </button>
        </div>
      )}
    </>
  );
}
