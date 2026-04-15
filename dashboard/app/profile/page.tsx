"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "../lib/api";

const PERSONAL_FIELDS = [
  { key: "first_name", label: "First Name", placeholder: "e.g. Abdoulie" },
  { key: "last_name", label: "Last Name", placeholder: "e.g. Balisa" },
  { key: "email", label: "Email", placeholder: "e.g. you@example.com" },
  { key: "phone", label: "Phone", placeholder: "e.g. +220 123 4567" },
  { key: "nationality", label: "Nationality", placeholder: "e.g. Gambian" },
  { key: "date_of_birth", label: "Date of Birth", placeholder: "e.g. 2000-01-01" },
  { key: "address", label: "Address", placeholder: "e.g. Banjul, Gambia" },
  { key: "linkedin", label: "LinkedIn URL", placeholder: "e.g. linkedin.com/in/yourname" },
];

export default function ProfilePage() {
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  // CV upload
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState("");
  const [dragOver, setDragOver] = useState(false);

  // Personal fields
  const [personal, setPersonal] = useState<Record<string, string>>({});
  const [bio, setBio] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Completeness
  const [completeness, setCompleteness] = useState(0);

  // Education
  const [showAddEdu, setShowAddEdu] = useState(false);
  const [newEdu, setNewEdu] = useState({
    institution: "", degree: "", field: "", start_date: "", end_date: "", gpa: "",
  });

  // Work
  const [showAddWork, setShowAddWork] = useState(false);
  const [newWork, setNewWork] = useState({
    company: "", title: "", start_date: "", end_date: "", description: "",
  });

  // Skills
  const [newSkill, setNewSkill] = useState("");

  const load = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([api.getProfile(), api.getCompleteness()]);
      setProfile(p);
      setCompleteness(c.completeness);
      setPersonal(p.personal || {});
      setBio(p.personal?.summary || "");
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // ---- CV Upload ----
  async function processCV(file: File) {
    setUploading(true);
    setUploadStatus("Reading your CV and extracting details...");
    try {
      const result = await api.uploadCV(file);
      const ext = result.extracted;
      setUploadStatus(
        `CV parsed! Found ${ext.work} job${ext.work !== 1 ? "s" : ""}, ${ext.education} education entr${ext.education !== 1 ? "ies" : "y"}, ${ext.skills} skills. All fields updated below.`
      );
      await load();
      setTimeout(() => setUploadStatus(""), 8000);
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : "Failed to parse CV. Try a different PDF.");
    }
    setUploading(false);
  }

  function handleCVInput(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) processCV(file);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file && (file.type === "application/pdf" || file.name.endsWith(".pdf"))) {
      processCV(file);
    }
  }

  // ---- Save personal ----
  async function handleSave() {
    setSaving(true);
    setSaveSuccess(false);
    const fields = Object.entries({ ...personal, summary: bio })
      .filter(([, v]) => v && v.trim())
      .map(([key, value]) => ({ key, value, category: "personal" }));
    await api.setProfileBatch(fields);
    await load();
    setSaving(false);
    setSaveSuccess(true);
    setTimeout(() => setSaveSuccess(false), 3000);
  }

  // ---- Education ----
  async function handleAddEdu() {
    if (!newEdu.institution && !newEdu.degree) return;
    await api.addEducation(newEdu);
    setNewEdu({ institution: "", degree: "", field: "", start_date: "", end_date: "", gpa: "" });
    setShowAddEdu(false);
    await load();
  }

  // ---- Work ----
  async function handleAddWork() {
    if (!newWork.company && !newWork.title) return;
    await api.addWork(newWork);
    setNewWork({ company: "", title: "", start_date: "", end_date: "", description: "" });
    setShowAddWork(false);
    await load();
  }

  // ---- Skills ----
  async function handleAddSkill() {
    if (!newSkill.trim()) return;
    await api.addSkill({ name: newSkill.trim() });
    setNewSkill("");
    await load();
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[40vh]">
        <div className="w-8 h-8 border-3 border-border border-t-accent rounded-full animate-spin" />
      </div>
    );
  }

  const skills = profile?.skills?.filter((s: any) => s.category !== "language") || [];
  const progressColor = completeness >= 100 ? "bg-green" : completeness >= 50 ? "bg-accent" : "bg-amber-400";

  return (
    <>
      {/* Profile Completeness */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-2xl font-bold">Your Profile</h1>
          <span className={`text-sm font-bold ${completeness >= 100 ? "text-green" : "text-text-secondary"}`}>
            {completeness}% complete
          </span>
        </div>
        <div className="w-full h-2.5 bg-surface rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${progressColor}`}
            style={{ width: `${completeness}%` }}
          />
        </div>
      </div>

      {/* CV Upload Area */}
      <div
        className={`relative rounded-xl border-2 border-dashed p-10 text-center mb-8 transition-colors ${
          dragOver
            ? "border-accent bg-accent/5"
            : "border-border hover:border-text-muted"
        }`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
        <svg className="w-12 h-12 text-text-muted mx-auto mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
        </svg>
        <p className="text-lg font-semibold mb-1">Upload your CV</p>
        <p className="text-sm text-text-muted mb-4">
          We'll extract your name, education, work history, and skills automatically.
        </p>
        <label className="inline-flex items-center gap-2 bg-accent hover:bg-accent-hover text-white text-sm px-5 py-2.5 rounded-lg cursor-pointer transition-colors">
          {uploading ? (
            <>
              <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Parsing...
            </>
          ) : (
            "Choose PDF"
          )}
          <input type="file" accept=".pdf" onChange={handleCVInput} className="hidden" disabled={uploading} />
        </label>

        {uploadStatus && (
          <div className={`mt-4 p-3 rounded-lg text-sm inline-block ${
            uploading ? "bg-accent/10 text-accent" : uploadStatus.includes("parsed") ? "bg-green/10 text-green" : "bg-red/10 text-red"
          }`}>
            {uploadStatus}
          </div>
        )}
      </div>

      {/* Personal Details */}
      <Section title="Personal Details">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {PERSONAL_FIELDS.map(({ key, label, placeholder }) => (
            <label key={key} className="block">
              <span className="text-xs text-text-muted mb-1 block">{label}</span>
              <input
                className="input"
                placeholder={placeholder}
                value={personal[key] || ""}
                onChange={(e) => setPersonal({ ...personal, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <label className="block mt-4">
          <span className="text-xs text-text-muted mb-1 block">Bio / About Me</span>
          <textarea
            className="input"
            rows={4}
            placeholder="A short summary about yourself — used for cover letters and personal statements."
            value={bio}
            onChange={(e) => setBio(e.target.value)}
          />
        </label>
      </Section>

      {/* Education */}
      <Section
        title="Education"
        action={
          <button onClick={() => setShowAddEdu(!showAddEdu)} className="text-xs text-accent hover:text-accent-hover">
            + Add
          </button>
        }
      >
        {profile?.education?.length === 0 && !showAddEdu && (
          <p className="text-sm text-text-muted">No education entries yet.</p>
        )}
        {profile?.education?.map((edu: any) => (
          <div key={edu.id} className="flex items-start justify-between py-3 border-b border-border last:border-0">
            <div>
              <p className="text-sm font-medium">
                {edu.degree}{edu.field ? ` in ${edu.field}` : ""}
              </p>
              <p className="text-xs text-text-muted">
                {edu.institution} &middot; {edu.start_date} - {edu.end_date || "Present"}
              </p>
              {edu.gpa && <p className="text-xs text-text-secondary mt-0.5">Grade: {edu.gpa}</p>}
            </div>
            <button
              onClick={() => { api.deleteEducation(edu.id).then(load); }}
              className="text-xs text-red hover:text-red/80 shrink-0"
            >
              Remove
            </button>
          </div>
        ))}
        {showAddEdu && (
          <div className="mt-3 space-y-3 border-t border-border pt-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <input className="input" placeholder="Institution" value={newEdu.institution} onChange={(e) => setNewEdu({ ...newEdu, institution: e.target.value })} />
              <input className="input" placeholder="Degree (e.g. BSc, MSc)" value={newEdu.degree} onChange={(e) => setNewEdu({ ...newEdu, degree: e.target.value })} />
              <input className="input" placeholder="Field of Study" value={newEdu.field} onChange={(e) => setNewEdu({ ...newEdu, field: e.target.value })} />
              <input className="input" placeholder="Grade (optional)" value={newEdu.gpa} onChange={(e) => setNewEdu({ ...newEdu, gpa: e.target.value })} />
              <input className="input" placeholder="Start Year (e.g. 2020)" value={newEdu.start_date} onChange={(e) => setNewEdu({ ...newEdu, start_date: e.target.value })} />
              <input className="input" placeholder="End Year (e.g. 2024)" value={newEdu.end_date} onChange={(e) => setNewEdu({ ...newEdu, end_date: e.target.value })} />
            </div>
            <div className="flex gap-2">
              <button onClick={handleAddEdu} className="bg-accent hover:bg-accent-hover text-white text-xs px-4 py-2 rounded-lg">
                Add Education
              </button>
              <button onClick={() => setShowAddEdu(false)} className="text-xs text-text-muted hover:text-text-secondary px-3 py-2">
                Cancel
              </button>
            </div>
          </div>
        )}
      </Section>

      {/* Work Experience */}
      <Section
        title="Work Experience"
        action={
          <button onClick={() => setShowAddWork(!showAddWork)} className="text-xs text-accent hover:text-accent-hover">
            + Add
          </button>
        }
      >
        {profile?.work?.length === 0 && !showAddWork && (
          <p className="text-sm text-text-muted">No work experience yet.</p>
        )}
        {profile?.work?.map((job: any) => (
          <div key={job.id} className="flex items-start justify-between py-3 border-b border-border last:border-0">
            <div>
              <p className="text-sm font-medium">{job.title} at {job.company}</p>
              <p className="text-xs text-text-muted">{job.start_date} - {job.end_date || "Present"}</p>
              {job.description && <p className="text-xs text-text-secondary mt-1 max-w-lg">{job.description}</p>}
            </div>
            <button
              onClick={() => { api.deleteWork(job.id).then(load); }}
              className="text-xs text-red hover:text-red/80 shrink-0"
            >
              Remove
            </button>
          </div>
        ))}
        {showAddWork && (
          <div className="mt-3 space-y-3 border-t border-border pt-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <input className="input" placeholder="Company" value={newWork.company} onChange={(e) => setNewWork({ ...newWork, company: e.target.value })} />
              <input className="input" placeholder="Role / Job Title" value={newWork.title} onChange={(e) => setNewWork({ ...newWork, title: e.target.value })} />
              <input className="input" placeholder="Start Date (e.g. 2023-01)" value={newWork.start_date} onChange={(e) => setNewWork({ ...newWork, start_date: e.target.value })} />
              <input className="input" placeholder="End Date (or Present)" value={newWork.end_date} onChange={(e) => setNewWork({ ...newWork, end_date: e.target.value })} />
            </div>
            <textarea
              className="input"
              rows={3}
              placeholder="Responsibilities — what did you do there?"
              value={newWork.description}
              onChange={(e) => setNewWork({ ...newWork, description: e.target.value })}
            />
            <div className="flex gap-2">
              <button onClick={handleAddWork} className="bg-accent hover:bg-accent-hover text-white text-xs px-4 py-2 rounded-lg">
                Add Experience
              </button>
              <button onClick={() => setShowAddWork(false)} className="text-xs text-text-muted hover:text-text-secondary px-3 py-2">
                Cancel
              </button>
            </div>
          </div>
        )}
      </Section>

      {/* Skills */}
      <Section title="Skills">
        <div className="flex flex-wrap gap-2 mb-4">
          {skills.map((s: any) => (
            <span
              key={s.id}
              className="inline-flex items-center gap-1.5 bg-accent/10 text-accent text-xs px-3 py-1.5 rounded-full"
            >
              {s.name}
              <button
                onClick={() => { api.deleteSkill(s.id).then(load); }}
                className="hover:text-red transition-colors"
              >
                x
              </button>
            </span>
          ))}
          {skills.length === 0 && <p className="text-sm text-text-muted">No skills yet. Type below and press Enter.</p>}
        </div>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="e.g. Python, Project Management, Data Analysis"
            value={newSkill}
            onChange={(e) => setNewSkill(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAddSkill()}
          />
          <button onClick={handleAddSkill} className="bg-accent hover:bg-accent-hover text-white text-xs px-4 py-2 rounded-lg shrink-0">
            Add
          </button>
        </div>
      </Section>

      {/* Save Button */}
      <div className="sticky bottom-4 flex justify-end mt-4 mb-8">
        <button
          onClick={handleSave}
          disabled={saving}
          className="bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm px-8 py-3 rounded-xl transition-colors shadow-lg shadow-accent/20 font-medium"
        >
          {saving ? "Saving..." : saveSuccess ? "Saved!" : "Save Profile"}
        </button>
      </div>
    </>
  );
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-surface rounded-xl border border-border p-5 mb-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </div>
  );
}
