import dayjs from "dayjs";

export function humanize(value?: string | null): string {
  if (!value) return "—";
  return value.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function formatTime(value?: string | null): string {
  return value ? dayjs(value).format("MMM D, HH:mm") : "—";
}

export function elapsed(start?: string | null, finish?: string | null): string {
  if (!start) return "Not started";
  const milliseconds = dayjs(finish ?? undefined).diff(dayjs(start));
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function compactId(value: string): string {
  const segment = value.includes(":") ? value.split(":").at(-1)! : value;
  return segment.length > 18 ? `${segment.slice(0, 8)}…${segment.slice(-6)}` : segment;
}

export function isTerminal(status?: string): boolean {
  return ["succeeded", "failed", "cancelled", "terminal"].includes(status ?? "");
}

export function statusTone(status?: string): "success" | "processing" | "warning" | "error" | "default" {
  if (["succeeded", "ready", "ready_to_run", "available", "completed"].includes(status ?? "")) return "success";
  if (["running", "queued", "claimed", "analyzing", "planning"].includes(status ?? "")) return "processing";
  if (["waiting_for_resource", "cancel_requested", "missing", "unavailable"].includes(status ?? "")) return "warning";
  if (["failed", "cancelled", "invalid"].includes(status ?? "")) return "error";
  return "default";
}

export function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}
