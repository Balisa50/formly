"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "../lib/api";

export default function ProfilePage() {
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [completeness, setCompleteness] = useState(0);

  // Personal fields
  const [personal, setPersonal] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

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
    try {
      await api.uploadCV(file);
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to parse CV");
    }
    setUploading(false);
  }

  async function savePersonal() {
    setSaving(true);
    const fields = Object.entries(personal).map(([key, value]) => ({ key, value, category: "personal" }));
    await api.setProfileBatch(fields);
    await load();
    setSaving(false);
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

  if (loading) return <p className="text-text-muted">Loading profile...</p>;

  return (
    <>
      <h1 className="text-2xl font-bold mb-1">Your Profile</h1>
      <p className="text-sm text-text-muted mb-6">Upload your CV to get started, or add details manually.</p>

      {/* Completeness */}
      <div className="flex items-center gap-3 mb-6">
        <div className="flex-1 h-2 bg-surface rounded-full overflow-hidden">
          <div className="h-full bg-accent rounded-full" style={{ width: `${completeness}%` }} />
        </div>
        <span className="text-sm font-bold">{completeness}%</span>
      </div>

      {/* CV Upload */}
      <div className="bg-surface rounded-xl border border-border p-5 mb-6">
        <h2 className="font-semibold mb-3">Upload CV</h2>
        <label className="inline-flex items-center gap-2 bg-accent hover:bg-accent-hover text-white text-sm px-4 py-2 rounded-lg cursor-pointer transition-colors">
          {uploading ? "Parsing..." : "Choose PDF"}
          <input type="file" accept=".pdf" onChange={handleCV} className="hidden" disabled={uploading} />
        </label>
      </div>

      {/* Personal Details */}
      <div className="bg-surface rounded-xl border border-border p-5 mb-6">
        <h2 className="font-semibold mb-4">Personal Details</h2>
        <div className="grid grid-cols-2 gap-4">
          {["first_name", "last_name", "email", "phone", "nationality", "date_of_birth", "address", "linkedin"].map((key) => (
            <label key={key} className="block">
              <span className="text-xs text-text-muted capitalize">{key.replace(/_/g, " ")}</span>
              <input
                className="input mt-1"
                value={personal[key] || ""}
                onChange={(e) => setPersonal({ ...personal, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <button
          onClick={savePersonal}
          disabled={saving}
          className="mt-4 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm px-4 py-2 rounded-lg transition-colors"
        >
          {saving ? "Saving..." : "Save"}
        </button>
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
          <p className="text-sm text-text-muted">No work experience yet</p>
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
          <p className="text-sm text-text-muted">No education entries yet</p>
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
          {profile?.skills?.length === 0 && <p className="text-sm text-text-muted">No skills yet</p>}
        </div>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="Add a skill..."
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
