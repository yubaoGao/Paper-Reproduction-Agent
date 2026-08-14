export type IntakeState =
  | "analyzing"
  | "ambiguous"
  | "waiting_for_resource"
  | "ready_to_run"
  | "queued"
  | "running"
  | "failed"
  | "terminal";

export type IntakePhase =
  | "pending"
  | "paper_parsing"
  | "paper_extracting"
  | "goal_resolving"
  | "waiting_for_clarification"
  | "repository_analyzing"
  | "aligning"
  | "preparing"
  | "ready_to_run"
  | "failed";

export type SessionStatus =
  | "active"
  | "awaiting_clarification"
  | "waiting_for_resource";

export type SessionExperimentStatus =
  | "not_selected"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type JobState =
  | "pending"
  | "planning"
  | "ready"
  | "queued"
  | "claimed"
  | "running"
  | "succeeded"
  | "failed"
  | "cancel_requested"
  | "cancelled";

export interface ResourceRequirement {
  requirement_id: string;
  resource_name: string;
  resource_type: "dataset" | "checkpoint" | "pretrained_model" | string;
  required: boolean;
  status: "available" | "missing" | "invalid" | string;
  preparation_hints: string[];
  source_urls: string[];
  expected_structure: string[];
  messages: string[];
}

export interface Intake {
  intake_id: string;
  state: IntakeState;
  current_phase?: IntakePhase | string | null;
  goal: string;
  repository_url: string;
  candidate_experiment_ids: string[];
  selected_experiment_ids: string[];
  clarification_questions: string[];
  required_resources: ResourceRequirement[];
  planning_status?: string | null;
  planning_blockers: Array<Record<string, unknown>>;
  waiting_reason?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  failed_phase?: IntakePhase | string | null;
  job_id?: string | null;
  session_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobDetail {
  job_id: string;
  session_id?: string | null;
  goal: string;
  selected_experiment_ids: string[];
  state: JobState;
  current_action?: string | null;
  progress: Record<string, unknown>;
  waiting_reason?: string | null;
  required_resources?: ResourceRequirement[];
  gpu_requirement?: Record<string, unknown> | null;
  gpu_allocation?: Record<string, unknown> | null;
  resource_adaptations: ResourceAdaptation[];
  attempts: number;
  retries: number;
  terminal_failure?: string | null;
  created_at: string;
  updated_at: string;
  enqueued_at?: string | null;
  claimed_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface ResourceAdaptation {
  adaptation_id?: string;
  reason?: string;
  impact?: string;
  semantic_impact?: string;
  original_config?: Record<string, unknown>;
  adapted_config?: Record<string, unknown>;
  effective_batch_before?: number;
  effective_batch_after?: number;
  [key: string]: unknown;
}

export interface ProductEvent {
  event_id: string;
  sequence: number;
  intake_id: string;
  job_id?: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface FinalMetric {
  name: string;
  status: "available" | "missing" | "unavailable" | string;
  value?: number | null;
  split?: string;
  unit?: string | null;
  checkpoint_reference?: string | null;
  epoch?: number | null;
  std?: number | null;
  evidence?: unknown[];
  provenance?: Record<string, unknown>;
}

export interface FinalResult {
  result_id: string;
  paper_experiment_id: string;
  reporting_metrics: FinalMetric[];
  evaluation_policy?: Record<string, unknown>;
  aggregation?: string;
  runs?: Array<Record<string, unknown>>;
  evidence?: unknown[];
  provenance?: Record<string, unknown>;
}

export interface MetricComparison {
  comparison_id: string;
  paper_experiment_id: string;
  paper_metric?: { original_name?: string; normalized_name?: string } | null;
  reproduced_metric?: { original_name?: string; normalized_name?: string } | null;
  paper_value?: number | null;
  reproduced_value?: number | null;
  absolute_difference?: number | null;
  relative_difference?: number | null;
  percentage_point_difference?: number | null;
  status: string;
  reason?: string;
  evidence_chain?: Record<string, unknown>;
}

export interface ExperimentComparison {
  paper_experiment_id: string;
  status: string;
  metric_comparisons: MetricComparison[];
  additional_metrics?: FinalMetric[];
  final_result_id?: string | null;
  execution_failure?: Record<string, unknown> | null;
}

export interface ComparisonReport {
  report_id: string;
  selection_mode: string;
  selected_experiment_ids: string[];
  experiments: ExperimentComparison[];
}

export interface ExperimentJobHistory {
  job_id: string;
  goal: string;
  status: SessionExperimentStatus | string;
  created_at: string;
  updated_at: string;
}

export interface SessionExperiment {
  experiment_id: string;
  name: string;
  experiment_type: string;
  status: SessionExperimentStatus | string;
  current_job_id?: string | null;
  job_history: ExperimentJobHistory[];
}

export interface ReproductionSession {
  session_id: string;
  status: SessionStatus | string;
  origin_intake_id: string;
  repository_url: string;
  repository_snapshot_id?: string | null;
  repository_commit_sha?: string | null;
  paper_content_hash: string;
  source_filename: string;
  goal?: string | null;
  candidate_experiment_ids: string[];
  selected_experiment_ids: string[];
  clarification_questions: string[];
  required_resources: ResourceRequirement[];
  planning_status?: string | null;
  planning_blockers: Array<Record<string, unknown>>;
  pending_job_id?: string | null;
  waiting_reason?: string | null;
  experiments: SessionExperiment[];
  jobs: JobDetail[];
  created_at: string;
  updated_at: string;
}

export interface ApiErrorBody {
  code?: string;
  message?: string;
  detail?: string;
}
