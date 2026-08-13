import { apiUrl, getPrincipal } from "./client";
import type { ProductEvent } from "./types";

export const PRODUCT_EVENT_TYPES = [
  "PAPER_ANALYSIS_STARTED", "PAPER_ANALYSIS_COMPLETED",
  "REPOSITORY_ANALYSIS_STARTED", "REPOSITORY_ANALYSIS_COMPLETED",
  "EXPERIMENT_SELECTION_RESOLVED", "CLARIFICATION_REQUIRED",
  "RESOURCE_REQUIRED", "RESOURCE_RESOLVED", "PLANNING_STARTED",
  "PLANNING_COMPLETED", "JOB_QUEUED", "JOB_CLAIMED", "GPU_WAITING",
  "GPU_ALLOCATED", "STEP_STARTED", "STEP_COMPLETED", "EPOCH_PROGRESS",
  "AGENT_PATCH_STARTED", "AGENT_PATCH_COMPLETED", "GPU_OOM",
  "RESOURCE_ADAPTED", "STEP_RETRYING", "FINAL_RESULT_ACQUIRED",
  "COMPARISON_COMPLETED", "JOB_SUCCEEDED", "JOB_FAILED", "JOB_CANCELLED",
] as const;

type StreamStatus = "connecting" | "connected" | "reconnecting" | "closed";

function cursorKey(jobId: string): string {
  return `repropilot.sse.${getPrincipal()}.${jobId}`;
}

function retryableStatus(status: number): boolean {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

export function parseProductEventBlock(
  block: string,
  lastSequence: number,
): ProductEvent | undefined {
  let eventType = "message";
  let lastEventId = "";
  const data: string[] = [];
  block.split(/\r?\n/).forEach((line) => {
    if (!line || line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const value = separator < 0 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "event") eventType = value;
    if (field === "id" && !value.includes("\0")) lastEventId = value;
    if (field === "data") data.push(value);
  });
  if (!data.length || !PRODUCT_EVENT_TYPES.includes(eventType as typeof PRODUCT_EVENT_TYPES[number])) return undefined;
  try {
    const event = JSON.parse(data.join("\n")) as ProductEvent;
    const sequence = Number(event.sequence ?? lastEventId);
    if (event.type !== eventType || !Number.isFinite(sequence) || sequence <= lastSequence) return undefined;
    event.sequence = sequence;
    return event;
  } catch {
    return undefined;
  }
}

export class ReproductionEventStream {
  private controller?: AbortController;
  private generation = 0;
  private stopped = true;
  private lastSequence: number;

  constructor(
    private readonly jobId: string,
    private readonly onEvent: (event: ProductEvent) => void,
    private readonly onStatus: (status: StreamStatus) => void,
  ) {
    this.lastSequence = Number(localStorage.getItem(cursorKey(jobId)) || 0);
  }

  connect(): void {
    this.close(false);
    this.stopped = false;
    const generation = ++this.generation;
    this.onStatus(this.lastSequence > 0 ? "reconnecting" : "connecting");
    void this.run(generation);
  }

  close(notify = true): void {
    this.stopped = true;
    this.generation += 1;
    this.controller?.abort();
    this.controller = undefined;
    if (notify) this.onStatus("closed");
  }

  private async run(generation: number): Promise<void> {
    let retryDelay = 1_000;
    while (!this.stopped && generation === this.generation) {
      this.controller = new AbortController();
      try {
        const headers: Record<string, string> = {
          Accept: "text/event-stream",
          "X-ReproPilot-Principal": getPrincipal(),
        };
        if (this.lastSequence > 0) headers["Last-Event-ID"] = String(this.lastSequence);
        const response = await fetch(
          apiUrl(`/api/v1/reproductions/${encodeURIComponent(this.jobId)}/events`),
          { headers, signal: this.controller.signal, cache: "no-store" },
        );
        if (!response.ok) {
          if (!retryableStatus(response.status)) {
            this.onStatus("closed");
            return;
          }
          throw new Error(`SSE request failed (${response.status})`);
        }
        if (!response.body || !response.headers.get("Content-Type")?.toLowerCase().startsWith("text/event-stream")) {
          throw new Error("SSE response is not a readable event stream");
        }
        this.onStatus("connected");
        retryDelay = 1_000;
        await this.consume(response.body, generation);
      } catch {
        if (this.stopped || generation !== this.generation || this.controller.signal.aborted) return;
        this.onStatus("reconnecting");
      }
      if (this.stopped || generation !== this.generation) return;
      this.onStatus("reconnecting");
      await new Promise((resolve) => window.setTimeout(resolve, retryDelay));
      retryDelay = Math.min(15_000, retryDelay * 2);
    }
  }

  private async consume(body: ReadableStream<Uint8Array>, generation: number): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!this.stopped && generation === this.generation) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.match(/\r?\n\r?\n/);
      while (boundary?.index !== undefined) {
        const block = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        this.dispatchBlock(block);
        boundary = buffer.match(/\r?\n\r?\n/);
      }
    }
  }

  private dispatchBlock(block: string): void {
    const event = parseProductEventBlock(block, this.lastSequence);
    if (!event) return;
    this.onEvent(event);
    this.lastSequence = event.sequence;
    localStorage.setItem(cursorKey(this.jobId), String(event.sequence));
  }
}

export type { StreamStatus };
