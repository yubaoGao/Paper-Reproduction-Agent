import {
  CheckCircleFilled, ClockCircleOutlined, ExperimentOutlined,
  FileSearchOutlined, LoadingOutlined, WarningFilled,
} from "@ant-design/icons";
import type { ProductEvent } from "../api/types";
import { formatTime, humanize, numberValue, stringValue } from "../utils/presentation";

const eventPresentation: Record<string, { actor: string; text: string; tone?: string }> = {
  PAPER_ANALYSIS_STARTED: { actor: "Paper Agent", text: "Reading the paper experiment structure" },
  PAPER_ANALYSIS_COMPLETED: { actor: "Paper Agent", text: "Paper experiment analysis completed" },
  REPOSITORY_ANALYSIS_STARTED: { actor: "Repository Agent", text: "Inspecting the repository implementation" },
  REPOSITORY_ANALYSIS_COMPLETED: { actor: "Repository Agent", text: "Repository analysis completed" },
  EXPERIMENT_SELECTION_RESOLVED: { actor: "Goal Resolver", text: "Experiment selection resolved" },
  CLARIFICATION_REQUIRED: { actor: "Goal Resolver", text: "Clarification is required", tone: "warning" },
  RESOURCE_REQUIRED: { actor: "Resource Agent", text: "An external resource is required", tone: "warning" },
  RESOURCE_RESOLVED: { actor: "Resource Agent", text: "External resource validated" },
  PLANNING_STARTED: { actor: "Planner", text: "Building the reproduction plan" },
  PLANNING_COMPLETED: { actor: "Planner", text: "Reproduction plan generated" },
  JOB_QUEUED: { actor: "Execution Queue", text: "Job entered the durable queue" },
  JOB_CLAIMED: { actor: "Execution Worker", text: "Job claimed by a worker" },
  GPU_WAITING: { actor: "GPU Scheduler", text: "Waiting for a suitable GPU allocation", tone: "waiting" },
  GPU_ALLOCATED: { actor: "GPU Scheduler", text: "GPU resources allocated" },
  STEP_STARTED: { actor: "Experiment Runner", text: "Experiment step started", tone: "running" },
  STEP_COMPLETED: { actor: "Experiment Runner", text: "Experiment step completed" },
  EPOCH_PROGRESS: { actor: "Training", text: "Epoch progress updated", tone: "running" },
  AGENT_PATCH_STARTED: { actor: "Patch Agent", text: "Preparing a bounded patch", tone: "running" },
  AGENT_PATCH_COMPLETED: { actor: "Patch Agent", text: "Bounded patch completed" },
  GPU_OOM: { actor: "Resource Agent", text: "GPU out of memory detected", tone: "error" },
  RESOURCE_ADAPTED: { actor: "Resource Agent", text: "Execution resources adapted", tone: "warning" },
  STEP_RETRYING: { actor: "Experiment Runner", text: "Retrying the step", tone: "warning" },
  FINAL_RESULT_ACQUIRED: { actor: "Result Resolver", text: "Canonical FinalResult acquired" },
  COMPARISON_COMPLETED: { actor: "Comparator", text: "Paper comparison completed" },
  JOB_SUCCEEDED: { actor: "ReproPilot", text: "Reproduction succeeded" },
  JOB_FAILED: { actor: "ReproPilot", text: "Reproduction failed", tone: "error" },
  JOB_CANCELLED: { actor: "ReproPilot", text: "Reproduction cancelled", tone: "warning" },
};

function detail(event: ProductEvent): string | undefined {
  const payload = event.payload;
  if (event.type === "EXPERIMENT_SELECTION_RESOLVED") {
    const ids = payload.selected_experiment_ids;
    if (Array.isArray(ids)) return `${ids.length} experiment${ids.length === 1 ? "" : "s"} selected`;
  }
  if (event.type === "EPOCH_PROGRESS") {
    const current = numberValue(payload.epoch ?? payload.current_epoch);
    const total = numberValue(payload.total_epochs);
    if (current !== undefined) return total ? `Epoch ${current} / ${total}` : `Epoch ${current}`;
  }
  if (event.type === "STEP_STARTED" || event.type === "STEP_COMPLETED" || event.type === "STEP_RETRYING") {
    return stringValue(payload.step_name ?? payload.step_id ?? payload.action);
  }
  if (event.type === "RESOURCE_REQUIRED") return stringValue(payload.resource_name);
  if (event.type === "RESOURCE_ADAPTED") return stringValue(payload.reason ?? payload.impact ?? payload.semantic_impact);
  if (event.type === "JOB_FAILED") return stringValue(payload.message);
  return undefined;
}

function EventIcon({ type, tone }: { type: string; tone?: string }) {
  if (tone === "error") return <WarningFilled />;
  if (tone === "warning") return <WarningFilled />;
  if (tone === "running") return <LoadingOutlined />;
  if (tone === "waiting") return <ClockCircleOutlined />;
  if (type.includes("PAPER") || type.includes("REPOSITORY")) return <FileSearchOutlined />;
  if (type.includes("STEP") || type.includes("EPOCH")) return <ExperimentOutlined />;
  return <CheckCircleFilled />;
}

export function ProductEventTimeline({ events }: { events: ProductEvent[] }) {
  if (!events.length) return null;
  return (
    <section className="agent-timeline" aria-label="Product activity">
      {events.map((event) => {
        const presentation = eventPresentation[event.type] ?? { actor: "ReproPilot", text: humanize(event.type) };
        return (
          <article className={`agent-event ${presentation.tone ?? "complete"}`} key={event.sequence}>
            <div className="agent-event-icon"><EventIcon type={event.type} tone={presentation.tone} /></div>
            <div className="agent-event-body">
              <div className="agent-event-topline"><strong>{presentation.actor}</strong><span>{formatTime(event.created_at)}</span></div>
              <div>{presentation.text}</div>
              {detail(event) && <small>{detail(event)}</small>}
            </div>
          </article>
        );
      })}
    </section>
  );
}
