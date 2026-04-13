const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", ...opts?.headers },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  // Health
  health: () => request<{ status: string }>("/api/health"),

  // Profile
  getProfile: () => request<{ personal: Record<string, string>; work: any[]; education: any[]; skills: any[] }>("/api/profile"),
  getCompleteness: () => request<{ completeness: number; filled: number; total: number }>("/api/profile/completeness"),
  setProfileField: (key: string, value: string, category = "personal") =>
    request("/api/profile", { method: "POST", body: JSON.stringify({ key, value, category }) }),
  setProfileBatch: (fields: { key: string; value: string; category: string }[]) =>
    request("/api/profile/batch", { method: "POST", body: JSON.stringify(fields) }),
  deleteProfileField: (key: string) => request(`/api/profile/${key}`, { method: "DELETE" }),

  // CV
  uploadCV: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/api/profile/cv`, { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  // Work
  addWork: (data: { company: string; title: string; start_date?: string; end_date?: string; description?: string }) =>
    request("/api/profile/work", { method: "POST", body: JSON.stringify(data) }),
  deleteWork: (id: number) => request(`/api/profile/work/${id}`, { method: "DELETE" }),

  // Education
  addEducation: (data: { institution: string; degree: string; field?: string; start_date?: string; end_date?: string; gpa?: string }) =>
    request("/api/profile/education", { method: "POST", body: JSON.stringify(data) }),
  deleteEducation: (id: number) => request(`/api/profile/education/${id}`, { method: "DELETE" }),

  // Skills
  addSkill: (data: { name: string; category?: string; proficiency?: string }) =>
    request("/api/profile/skills", { method: "POST", body: JSON.stringify(data) }),
  deleteSkill: (id: number) => request(`/api/profile/skills/${id}`, { method: "DELETE" }),

  // Form scanning
  scanForm: (url: string) =>
    request<{ fields: any[]; page_context: string; count: number }>("/api/forms/scan", { method: "POST", body: JSON.stringify({ url }) }),

  // Matching
  matchFields: (url: string, fields: any[], page_context: string) =>
    request<{ matches: any[]; auto_filled: number; needs_input: number; needs_essay: number }>("/api/forms/match", { method: "POST", body: JSON.stringify({ url, fields, page_context }) }),

  // Auto-fill (smart inference for unknown fields)
  autoFill: (matches: any[], page_context = "") =>
    request<{ auto_filled: any[]; still_unknown: any[] }>("/api/forms/autofill", { method: "POST", body: JSON.stringify({ matches, page_context }) }),

  // Gap filling
  getGapQuestion: (label: string, field_type: string, selector: string, page_context = "") =>
    request<{ question: string }>("/api/forms/gap-question", { method: "POST", body: JSON.stringify({ label, field_type, selector, page_context }) }),
  saveGapAnswer: (label: string, selector: string, field_type: string, answer: string) =>
    request("/api/forms/gap-answer", { method: "POST", body: JSON.stringify({ label, selector, field_type, answer }) }),

  // Essay
  generateEssay: (prompt: string, page_context = "", max_length?: number) =>
    request<{ essay: string }>("/api/forms/essay", { method: "POST", body: JSON.stringify({ prompt, page_context, max_length }) }),

  // Applications
  listApplications: () => request<any[]>("/api/applications"),
  logApplication: (url: string, title = "", fields = {}) =>
    request<{ ok: boolean; id: number }>("/api/applications", { method: "POST", body: JSON.stringify({ url, title, fields }) }),

  // Stats
  getStats: () => request<{ total_applications: number; submitted: number; profile_fields: number; work_entries: number; education_entries: number; skills_count: number }>("/api/stats"),
};
