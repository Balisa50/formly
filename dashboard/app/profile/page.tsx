"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "../lib/api";

const PERSONAL_FIELDS = [
  { key: "first_name", label: "First Name", placeholder: "e.g. Abdoulie" },
  { key: "last_name", label: "Last Name", placeholder: "e.g. Balisa" },
  { key: "email", label: "Email", placeholder: "e.g. you@example.com" },
  { key: "phone", label: "Phone", placeholder: "e.g. +220 123 4567" },
  { key: "nationality", label: "Nationality", placeholder: "e.g. Gambian" },
  { key: "date_of_birth", label: "Date of Birth", placeholder: "e.g. 01/01/2000" },
  { key: "address", label: "Address", placeholder: "e.g. Banjul, Gambia" },
  { key: "linkedin", label: "LinkedIn", placeholder: "e.g. linkedin.com/in/yourname" },
];

export default function ProfilePage() {
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<string>("");
  const [completeness, setCompleteness] = useState(0);

  // Personal fields
  const [personal, setPersonal] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // New work
  const [showAddWork, setShowAddWork] = useState(false);
  const [newWork, setNewWork] = useState({ company: "", title: "", start_date: "", end_date: "", description: "" });

  // New education
  const [showAddEdu, setShowAddEdu] = useState(false);
  const [newEdu, setNewEdu] = useState({ institution: "", degree: "", field: "", start_date: "", end_date: "", gpa: "" });

  // New skill
  const [newSkill, setNewSkill] = useState("");

  const load = useCallback(async () => {
    try {
      const [p, c] = await Promise.all([api.getProfile(), api.getCompleteness()]);
      setProfile(p);
      setCompleteness(c.completeness);
      setPersonal(p.personal || {});
    } catch {
      // offline
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function handleCV(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadStatus("Reading your CV and extracting your details...");
    try {
      await api.uploadCV(file);
      setUploadStatus("CV parsed successfully! Your details have been filled in.");
      await load();
      // Auto-clear success after 5 seconds
      setTimeout(() => setUploadStatus(""), 5000);
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : "Failed to parse CV. Try a different PDF.");
    }
    setUploading(false);
  }

  async function savePersonal() {
    setSaving(true);
    setSaveSuccess(false);
    const fields = Object.entries(personal).map(([key, value]) => ({ key, value, category: "personal" }));
    await api.setProfileBatch(fields);
    await load();
    setSaving(false);
    setSaveSuccess(true);
    setTimeout(() => setSaveSuccess(false), 3000);
  }

  async function handleAddWork() {
    if (!newWork.company && !newWork.title) return;
    await api.addWork(newWork);
    setNewWork({ company: "", title: "", start_date: "", end_date: "", description: "" });
    setShowAddWork(false);
    await load();
  }

  async function handleAddEdu() {
    if (!newEdu.institution && !newEdu.degree) return;
    await api.addEducation(newEdu);
    setNewEdu({ institution: "", degree: "", field: "", start_date: "", end_date: "", gpa: "" });
    setShowAddEdu(false);
    await load();
  }

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

  return (
    <>
      <h1 className="text-2xl font-bold mb-1">Your Profile</h1>
      <p className="text-sm text-text-muted mb-6">Upload your CV to get started, or add details manually.</p>

      {/* Completeness */}
      <div className="flex items-center gap-3 mb-6">
        <div className="flex-1 h-2.5 bg-surface rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              completeness < 30 ? "bg-red" : completeness < 70 ? "bg-amber-400" : "bg-green"
            }`}
            style={{ width: `${completeness}%` }}
          />
        </div>
        <span className="text-sm font-bold">{completeness}%</span>
      </div>

      {/* CV Upload */}
      <div className="bg-surface rounded-xl border border-border p-5 mb-6">
        <h2 className="font-semibold mb-3">Upload CV</h2>
        <p className="text-sm text-text-muted mb-3">
          Upload a PDF and Formly will automatically extract your name, contact info, education, work experience, and skills.
        </p>

        <label className="inline-flex items-center gap-2 bg-accent hover:bg-accent-hover text-white text-sm px-4 py-2.5 rounded-lg cursor-pointer transition-colors">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
          {uploading ? "Parsing..." : "Choose PDF"}
          <input type="file" accept=".pdf" onChange={handleCV} className="hidden" disabled={uploading} />
        </label>

        {uploadStatus && (
          <div className={`mt-3 p-3 rounded-lg text-sm flex items-center gap-2 ${
            uploading
              ? "bg-accent/10 text-accent"
              : uploadStatus.includes("success")
                ? "bg-green/10 text-green"
                : "bg-red/10 text-red"
          }`}>
            {uploading && (
              <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin shrink-0" />
            )}
            {uploadStatus}
          </div>
        )}
      </div>

      {/* Personal Details */}
      <div className="bg-surface rounded-xl border border-border p-5 mb-6">
        <h2 className="font-semibold mb-4">Personal Details</h2>
        <div className="grid grid-cols-2 gap-4">
          {PERSONAL_FIELDS.map(({ key, label, placeholder }) => (
            <label key={key} className="block">
              <span className="text-xs text-text-muted">{label}</span>
              <input
                className="input mt-1"
                placeholder={placeholder}
                value={personal[key] || ""}
                onChange={(e) => setPersonal({ ...personal, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <div className="flex items-center gap-3 mt-4">
          <button
            onClick={savePersonal}
            disabled={saving}
            className="bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm px-4 py-2 rounded-lg transition-colors"
          >
            {saving ? "Saving..." : "Save"}
          </button>
          {saveSuccess && (
            <span className="text-sm text-green">Saved!</span>
          )}
        </div>
      </div>

      {/* Work Experience */}
      <div className="bg-surface rounded-xl border border-border p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold">Work Experience</h2>
          <button onClick={() => setShowAddWork(!showAddWork)} className="text-xs text-accent hover:text-accent-hover">
            + Add
          </button>
        </div>
        {profile?.work?.length === 0 && !showAddWork && (
          <p className="text-sm text-text-muted">No work experience yet — add your jobs so Formly can fill experience fields automatically.</p>
        )}
        {profile?.work?.map((job: any) => (
          <div key={job.id} className="flex items-start justify-between py-2 border-b border-border last:border-0">
            <div>
              <p className="text-sm font-medium">{job.title} at {job.company}</p>
              <p className="text-xs text-text-muted">{job.start_date} — {job.end_date || "Present"}</p>
              {job.description && <p className="text-xs text-text-secondary mt-1">{job.description}</p>}
            </div>
            <button onClick={() => { api.deleteWork(job.id).then(load); }} className="text-xs text-red hover:text-red/80">Delete</button>
          </div>
        ))}
        {showAddWork && (
          <div className="mt-3 space-y-3 border-t border-border pt-3">
            <div className="grid grid-cols-2 gap-3">
              <input className="input" placeholder="Company" value={newWork.company} onChange={(e) => setNewWork({ ...newWork, company: e.target.value })} />
              <input className="input" placeholder="Job Title" value={newWork.title} onChange={(e) => setNewWork({ ...newWork, title: e.target.value })} />
              <input className="input" placeholder="Start (2023-01)" value={newWork.start_date} onChange={(e) => setNewWork({ ...newWork, start_date: e.target.value })} />
              <input className="input" placeholder="End (Present)" value={newWork.end_date} onChange={(e) => setNewWork({ ...newWork, end_date: e.target.value })} />
            </div>
            <textarea className="input" rows={2} placeholder="Description" value={newWork.description} onChange={(e) => setNewWork({ ...newWork, description: e.target.value })} />
            <button onClick={handleAddWork} className="bg-accent hover:bg-accent-hover text-white text-xs px-3 py-1.5 rounded-lg">Add</button>
          </div>
        )}
      </div>

      {/* Education */}
      <div className="bg-surface rounded-xl border border-border p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold">Education</h2>
          <button onClick={() => setShowAddEdu(!showAddEdu)} className="text-xs text-accent hover:text-accent-hover">
            + Add
          </button>
        </div>
        {profile?.education?.length === 0 && !showAddEdu && (
          <p className="text-sm text-text-muted">No education entries yet — add your qualifications so forms auto-fill correctly.</p>
        )}
        {profile?.education?.map((edu: any) => (
          <div key={edu.id} className="flex items-start justify-between py-2 border-b border-border last:border-0">
            <div>
              <p className="text-sm font-medium">{edu.degree} in {edu.field}</p>
              <p className="text-xs text-text-muted">{edu.institution} &middot; {edu.start_date} — {edu.end_date}</p>
              {edu.gpa && <p className="text-xs text-text-secondary">GPA: {edu.gpa}</p>}
            </div>
            <button onClick={() => { api.deleteEducation(edu.id).then(load); }} className="text-xs text-red hover:text-red/80">Delete</button>
          </div>
        ))}
        {showAddEdu && (
          <div className="mt-3 space-y-3 border-t border-border pt-3">
            <div className="grid grid-cols-2 gap-3">
              <input className="input" placeholder="Institution" value={newEdu.institution} onChange={(e) => setNewEdu({ ...newEdu, institution: e.target.value })} />
              <input className="input" placeholder="Degree" value={newEdu.degree} onChange={(e) => setNewEdu({ ...newEdu, degree: e.target.value })} />
              <input className="input" placeholder="Field of Study" value={newEdu.field} onChange={(e) => setNewEdu({ ...newEdu, field: e.target.value })} />
              <input className="input" placeholder="GPA" value={newEdu.gpa} onChange={(e) => setNewEdu({ ...newEdu, gpa: e.target.value })} />
              <input className="input" placeholder="Start (2020-09)" value={newEdu.start_date} onChange={(e) => setNewEdu({ ...newEdu, start_date: e.target.value })} />
              <input className="input" placeholder="End (2024-06)" value={newEdu.end_date} onChange={(e) => setNewEdu({ ...newEdu, end_date: e.target.value })} />
            </div>
            <button onClick={handleAddEdu} className="bg-accent hover:bg-accent-hover text-white text-xs px-3 py-1.5 rounded-lg">Add</button>
          </div>
        )}
      </div>

      {/* Skills */}
      <div className="bg-surface rounded-xl border border-border p-5">
        <h2 className="font-semibold mb-4">Skills</h2>
        <div className="flex flex-wrap gap-2 mb-4">
          {profile?.skills?.map((s: any) => (
            <span key={s.id} className="inline-flex items-center gap-1 bg-surface-elevated text-text-secondary text-xs px-2.5 py-1 rounded-lg">
              {s.name}
              <button onClick={() => { api.deleteSkill(s.id).then(load); }} className="text-text-muted hover:text-red ml-1">x</button>
            </span>
          ))}
          {profile?.skills?.length === 0 && <p className="text-sm text-text-muted">No skills yet — add your key skills so Formly can match them to form fields.</p>}
        </div>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="e.g. Python, Data Analysis, Project Management"
            value={newSkill}
            onChange={(e) => setNewSkill(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAddSkill()}
          />
          <button onClick={handleAddSkill} className="bg-accent hover:bg-accent-hover text-white text-xs px-3 py-2 rounded-lg">Add</button>
        </div>
      </div>
    </>
  );
}
