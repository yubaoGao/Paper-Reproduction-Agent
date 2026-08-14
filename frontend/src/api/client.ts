import type {
  ApiErrorBody,
  ComparisonReport,
  FinalResult,
  Intake,
  JobDetail,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const PRINCIPAL_KEY = "repropilot.principal";
export const DEFAULT_PRINCIPAL = "local-researcher";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function getPrincipal(): string {
  return localStorage.getItem(PRINCIPAL_KEY) || DEFAULT_PRINCIPAL;
}

export function setPrincipal(value: string): void {
  localStorage.setItem(PRINCIPAL_KEY, value.trim() || DEFAULT_PRINCIPAL);
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-ReproPilot-Principal", getPrincipal());
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(apiUrl(path), { ...init, headers });
  if (!response.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // Keep the HTTP status when a proxy returns a non-JSON error page.
    }
    throw new ApiError(
      response.status,
      body.code ?? "request_failed",
      body.message ?? body.detail ?? `请求失败（${response.status}）`,
    );
  }
  return (await response.json()) as T;
}

export const api = {
  listJobs: () => request<JobDetail[]>("/api/v1/reproductions"),
  getJob: (jobId: string) => request<JobDetail>(`/api/v1/reproductions/${encodeURIComponent(jobId)}`),
  getIntake: (intakeId: string) => request<Intake>(`/api/v1/reproductions/intakes/${encodeURIComponent(intakeId)}`),
  createIntake: (input: { pdf: File; repositoryUrl: string; goal: string }) => {
    const form = new FormData();
    form.append("paper_pdf", input.pdf);
    form.append("repository_url", input.repositoryUrl);
    form.append("goal", input.goal);
    return request<Intake>("/api/v1/reproductions/intakes", { method: "POST", body: form });
  },
  clarify: (intakeId: string, answers: string[]) =>
    request<Intake>(`/api/v1/reproductions/intakes/${encodeURIComponent(intakeId)}/clarifications`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    }),
  submitResource: (intakeId: string, requirementId: string, hostPath: string) =>
    request<Intake>(`/api/v1/reproductions/intakes/${encodeURIComponent(intakeId)}/resources`, {
      method: "POST",
      body: JSON.stringify({ requirement_id: requirementId, host_path: hostPath }),
    }),
  start: (intakeId: string) =>
    request<JobDetail>(`/api/v1/reproductions/intakes/${encodeURIComponent(intakeId)}/start`, { method: "POST" }),
  cancel: (jobId: string) =>
    request<JobDetail>(`/api/v1/reproductions/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),
  getResults: (jobId: string) => request<FinalResult[]>(`/api/v1/reproductions/${encodeURIComponent(jobId)}/results`),
  getComparison: (jobId: string) => request<ComparisonReport>(`/api/v1/reproductions/${encodeURIComponent(jobId)}/comparison`),
};
