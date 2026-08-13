import { useMemo } from "react";
import {
  AimOutlined, ApartmentOutlined, CloudServerOutlined, DatabaseOutlined,
  ExperimentOutlined, FileDoneOutlined, FileSearchOutlined, HistoryOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import {
  Alert, Badge, Card, Collapse, Descriptions, Empty, Progress,
  Table, Tabs, Tag, Timeline, Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type {
  ComparisonReport, FinalMetric, FinalResult, Intake, JobDetail,
  MetricComparison, ProductEvent, ResourceAdaptation,
} from "../api/types";
import { compactId, elapsed, formatTime, humanize, numberValue, stringValue } from "../utils/presentation";
import { StatusPill } from "./StatusPill";

interface Props {
  intake?: Intake;
  job?: JobDetail;
  events: ProductEvent[];
  results?: FinalResult[];
  comparison?: ComparisonReport;
  resultsLoading: boolean;
}

function Overview({ intake, job }: Pick<Props, "intake" | "job">) {
  const selected = job?.selected_experiment_ids ?? intake?.selected_experiment_ids ?? [];
  const completed = numberValue(job?.progress.completed_steps) ?? 0;
  const total = numberValue(job?.progress.total_steps) ?? selected.length;
  return (
    <div className="inspector-section">
      <div className="overview-status-card">
        <span>Overall status</span>
        <div><StatusPill status={job?.state ?? intake?.state} /></div>
        <p>{job?.current_action ? humanize(job.current_action) : intake?.waiting_reason ?? "Waiting for the next product transition"}</p>
      </div>
      <div className="overview-progress">
        <div><span>Progress</span><strong>{completed} / {total}</strong></div>
        <Progress percent={total ? Math.round(completed / total * 100) : 0} showInfo={false} />
      </div>
      <Descriptions column={1} size="small" colon={false} className="detail-descriptions">
        <Descriptions.Item label="Selected experiments">{selected.length}</Descriptions.Item>
        <Descriptions.Item label="Queue state">{humanize(job?.state ?? intake?.state)}</Descriptions.Item>
        <Descriptions.Item label="Current action">{humanize(job?.current_action)}</Descriptions.Item>
        <Descriptions.Item label="Elapsed">{elapsed(job?.started_at, job?.finished_at)}</Descriptions.Item>
        <Descriptions.Item label="Started">{formatTime(job?.started_at)}</Descriptions.Item>
        <Descriptions.Item label="Finished">{formatTime(job?.finished_at)}</Descriptions.Item>
      </Descriptions>
      {job?.terminal_failure && <Alert type="error" showIcon message="Terminal failure" description={job.terminal_failure} />}
      {intake?.planning_blockers?.map((blocker, index) => (
        <Alert key={index} type="warning" showIcon message={String(blocker.code ?? "Planning blocker")} description={String(blocker.message ?? "Requires attention")} />
      ))}
    </div>
  );
}

function Experiments({ intake, job, events }: Pick<Props, "intake" | "job" | "events">) {
  const selected = job?.selected_experiment_ids ?? intake?.selected_experiment_ids ?? [];
  const statusByExperiment = new Map<string, string>();
  events.forEach((event) => {
    const id = stringValue(event.payload.experiment_id ?? event.payload.paper_experiment_id);
    if (id) statusByExperiment.set(id, event.type === "STEP_COMPLETED" ? "completed" : event.type === "STEP_STARTED" ? "running" : statusByExperiment.get(id) ?? "selected");
  });
  return (
    <div className="inspector-section">
      <p className="section-intro">Only experiments locked by the Goal Resolver are shown here.</p>
      {selected.length ? selected.map((id, index) => (
        <Card size="small" className="experiment-card" key={id}>
          <div className="experiment-number">{String(index + 1).padStart(2, "0")}</div>
          <div className="experiment-info"><strong>{id}</strong><span>Selected experiment</span></div>
          <StatusPill status={statusByExperiment.get(id) ?? "selected"} />
        </Card>
      )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Experiment scope is not resolved yet" />}
    </div>
  );
}

interface StepView { id: string; label: string; status: string; attempts: number; at: string; }

function stepViews(events: ProductEvent[], job?: JobDetail): StepView[] {
  const steps = new Map<string, StepView>();
  events.forEach((event) => {
    if (!["STEP_STARTED", "STEP_COMPLETED", "STEP_RETRYING"].includes(event.type)) return;
    const id = stringValue(event.payload.step_id ?? event.payload.action) ?? `step-${event.sequence}`;
    const current = steps.get(id) ?? { id, label: stringValue(event.payload.step_name ?? event.payload.action) ?? id, status: "waiting", attempts: 0, at: event.created_at };
    current.status = event.type === "STEP_COMPLETED" ? "completed" : event.type === "STEP_STARTED" ? "running" : "retrying";
    if (event.type === "STEP_STARTED" || event.type === "STEP_RETRYING") current.attempts += 1;
    current.at = event.created_at;
    steps.set(id, current);
  });
  if (!steps.size && job?.current_action) {
    steps.set(job.current_action, { id: job.current_action, label: job.current_action, status: job.state === "running" ? "running" : "waiting", attempts: job.attempts, at: job.updated_at });
  }
  return [...steps.values()];
}

function AdaptationCard({ adaptation }: { adaptation: ResourceAdaptation }) {
  const original = adaptation.original_config ?? {};
  const adapted = adaptation.adapted_config ?? {};
  const impact = stringValue(adaptation.impact ?? adaptation.semantic_impact) ?? "unknown";
  const controlled = impact.toLowerCase() === "controlled_deviation";
  return (
    <Card size="small" className={`adaptation-card ${controlled ? "controlled" : ""}`}>
      <div className="adaptation-heading"><WarningOutlined /><strong>Resource adaptation</strong><Tag color={controlled ? "gold" : "green"}>{humanize(impact)}</Tag></div>
      {controlled && <Alert type="warning" showIcon message="Controlled scientific deviation" description="The adaptation may affect reproduction semantics. Review its evidence before interpreting results." />}
      <div className="adaptation-grid">
        <div><span>Original</span>{Object.entries(original).slice(0, 6).map(([key, value]) => <code key={key}>{key} = {String(value)}</code>)}</div>
        <div className="adaptation-arrow">→</div>
        <div><span>Adapted</span>{Object.entries(adapted).slice(0, 6).map(([key, value]) => <code key={key}>{key} = {String(value)}</code>)}</div>
      </div>
      {(adaptation.effective_batch_before !== undefined || adaptation.effective_batch_after !== undefined) && <p>Effective batch · <strong>{String(adaptation.effective_batch_before ?? "—")} → {String(adaptation.effective_batch_after ?? "—")}</strong></p>}
    </Card>
  );
}

function Execution({ job, events }: Pick<Props, "job" | "events">) {
  const steps = useMemo(() => stepViews(events, job), [events, job]);
  const adaptations = [
    ...(job?.resource_adaptations ?? []),
    ...events.filter((event) => event.type === "RESOURCE_ADAPTED").map((event) => event.payload as ResourceAdaptation),
  ];
  const oom = events.some((event) => event.type === "GPU_OOM");
  const epoch = [...events].reverse().find((event) => event.type === "EPOCH_PROGRESS");
  const currentEpoch = numberValue(epoch?.payload.epoch ?? epoch?.payload.current_epoch);
  const totalEpochs = numberValue(epoch?.payload.total_epochs);
  const runtimeLines = events.flatMap((event) => {
    const lines = event.payload.log_lines;
    if (Array.isArray(lines)) return lines.filter((line): line is string => typeof line === "string").slice(-100);
    const message = stringValue(event.payload.runtime_message);
    return message ? [message] : [];
  });
  return (
    <div className="inspector-section execution-section">
      {epoch && (
        <Card size="small" className="epoch-card">
          <div className="epoch-heading"><span>Live training progress</span><strong>Epoch {currentEpoch ?? "—"} / {totalEpochs ?? "—"}</strong></div>
          <Progress percent={currentEpoch && totalEpochs ? Math.min(100, Math.round(currentEpoch / totalEpochs * 100)) : 0} status="active" />
          <div className="epoch-facts">
            {stringValue(epoch.payload.metric_name) && <span>Current · {String(epoch.payload.metric_name)} = {String(epoch.payload.metric_value ?? "—")}</span>}
            {epoch.payload.best_epoch !== undefined && <span>Best checkpoint · Epoch {String(epoch.payload.best_epoch)}</span>}
            {epoch.payload.best_metric !== undefined && <span>Best selection metric · {String(epoch.payload.best_metric)}</span>}
          </div>
          <Typography.Text type="secondary">Live metrics are process signals and are not canonical FinalResult.</Typography.Text>
        </Card>
      )}
      <div className="dag-label"><ApartmentOutlined /> Action DAG</div>
      {steps.length ? (
        <div className="execution-dag">
          {steps.map((step, index) => (
            <div className="dag-node-wrap" key={step.id}>
              <div className={`dag-node ${step.status}`}>
                <span className="dag-dot" />
                <div><strong>{humanize(step.label)}</strong><small>{humanize(step.status)} · {step.attempts} attempt{step.attempts === 1 ? "" : "s"}</small></div>
              </div>
              {index < steps.length - 1 && <div className="dag-connector">↓</div>}
            </div>
          ))}
        </div>
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Execution steps appear when the worker starts" />}
      {oom && <Alert type="error" showIcon message="GPU OOM" description="The resource agent is evaluating a bounded adaptation." />}
      {adaptations.map((adaptation, index) => <AdaptationCard adaptation={adaptation} key={String(adaptation.adaptation_id ?? index)} />)}
      <Collapse
        ghost
        items={[{
          key: "logs",
          label: <span><HistoryOutlined /> View runtime logs</span>,
          children: runtimeLines.length
            ? <pre className="runtime-log">{runtimeLines.join("\n")}</pre>
            : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No controlled runtime log events available" />,
        }]}
      />
    </div>
  );
}

function Resources({ intake, job, events }: Pick<Props, "intake" | "job" | "events">) {
  const resources = job?.required_resources ?? intake?.required_resources ?? [];
  const waiting = [...events].reverse().find((event) => event.type === "GPU_WAITING");
  const allocation = job?.gpu_allocation;
  return (
    <div className="inspector-section">
      <Card size="small" className="gpu-summary-card">
        <div className="gpu-summary-heading"><CloudServerOutlined /><strong>Current task GPU</strong>{waiting ? <Badge status="warning" text="Waiting" /> : allocation ? <Badge status="processing" text="Assigned" /> : <Badge status="default" text="Backend managed" />}</div>
        <Descriptions column={1} size="small" colon={false}>
          <Descriptions.Item label="Requirement">{job?.gpu_requirement ? "Declared by execution plan" : "Not reported"}</Descriptions.Item>
          <Descriptions.Item label="Allocation">{allocation ? String(allocation.device_ids ?? allocation.allocated_gpu_ids ?? allocation.gpu_ids ?? "Allocated") : "None"}</Descriptions.Item>
          <Descriptions.Item label="Scheduler">{waiting ? String(waiting.payload.reason ?? "WAITING_FOR_GPU") : "No active wait"}</Descriptions.Item>
        </Descriptions>
        <Typography.Text type="secondary">Only resources associated with this reproduction are shown.</Typography.Text>
      </Card>
      <div className="section-title"><DatabaseOutlined /> External resources</div>
      {resources.length ? resources.map((resource) => (
        <Card size="small" key={resource.requirement_id} className="inspector-resource-card">
          <div><strong>{resource.resource_name}</strong><span>{humanize(resource.resource_type)}</span></div>
          <StatusPill status={resource.status} />
        </Card>
      )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No external resources reported" />}
      {(job?.resource_adaptations.length ?? 0) > 0 && <Alert type="info" showIcon message={`${job?.resource_adaptations.length} resource adaptation${job?.resource_adaptations.length === 1 ? "" : "s"} recorded`} />}
    </div>
  );
}

function metricName(item: MetricComparison): string {
  return item.paper_metric?.original_name ?? item.reproduced_metric?.original_name ?? item.paper_metric?.normalized_name ?? item.reproduced_metric?.normalized_name ?? "Unnamed metric";
}

function valueCell(value?: number | null): string {
  return value === null || value === undefined ? "—" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function Results({ results, comparison, loading }: { results?: FinalResult[]; comparison?: ComparisonReport; loading: boolean }) {
  const columns: ColumnsType<MetricComparison> = [
    { title: "Metric", render: (_, item) => <strong>{metricName(item)}</strong> },
    { title: "Paper", dataIndex: "paper_value", render: valueCell },
    { title: "Reproduced", dataIndex: "reproduced_value", render: valueCell },
    { title: "Difference", dataIndex: "absolute_difference", render: valueCell },
    { title: "Status", dataIndex: "status", render: (status: string) => <StatusPill status={status} /> },
  ];
  const compared = comparison?.experiments.flatMap((experiment) => experiment.metric_comparisons) ?? [];
  const additional = comparison?.experiments.flatMap((experiment) => experiment.additional_metrics ?? []) ?? [];
  const resultMetrics = results?.flatMap((result) => result.reporting_metrics) ?? [];
  const missing = resultMetrics.filter((metric) => metric.status !== "available");
  return (
    <div className="inspector-section results-section">
      {loading && <Card loading />}
      {!loading && compared.length > 0 && <Table rowKey="comparison_id" size="small" pagination={false} scroll={{ x: 540 }} columns={columns} dataSource={compared} />}
      {!loading && !compared.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Comparison is available after canonical results are resolved" />}
      {missing.map((metric) => <MetricStatusCard metric={metric} key={`${metric.name}-${metric.split}`} />)}
      {additional.length > 0 && (
        <div className="additional-metrics">
          <h4>Additional reproduced metrics</h4>
          <p>Repository metrics without a paper claim are shown separately and are not fabricated comparisons.</p>
          {additional.map((metric) => <MetricStatusCard metric={metric} key={`${metric.name}-${metric.split}`} />)}
        </div>
      )}
    </div>
  );
}

function MetricStatusCard({ metric }: { metric: FinalMetric }) {
  return (
    <div className="metric-status-card">
      <div><strong>{metric.name}</strong><span>{metric.split ?? "Reported split"}</span></div>
      <div className="metric-value">{metric.status === "available" ? valueCell(metric.value) : "—"}</div>
      <StatusPill status={metric.status} />
    </div>
  );
}

function Evidence({ comparison, results, job }: Pick<Props, "comparison" | "results" | "job">) {
  if (!comparison) return <div className="inspector-section"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Evidence chains appear with the comparison report" /></div>;
  return (
    <div className="inspector-section evidence-section">
      <Alert type="info" showIcon message="Traceable scientific evidence" description="Each chain connects the selected experiment and paper claim to the exact reproduced result. Private model reasoning is never displayed." />
      <Collapse
        accordion
        items={comparison.experiments.map((experiment) => ({
          key: experiment.paper_experiment_id,
          label: <div className="evidence-collapse-label"><FileSearchOutlined /><span>{experiment.paper_experiment_id}</span><StatusPill status={experiment.status} /></div>,
          children: (
            <div className="evidence-chain">
              <Timeline items={[
                { color: "blue", children: <><strong>Selected experiment</strong><p>{experiment.paper_experiment_id}</p></> },
                { color: "blue", children: <><strong>Evaluation policy</strong><p>{humanize(comparison.selection_mode)} selection · {results?.find((result) => result.paper_experiment_id === experiment.paper_experiment_id)?.aggregation ?? "Backend-defined aggregation"}</p></> },
                { color: "blue", children: <><strong>Checkpoint, epoch, and seeds</strong><p>{evidenceSummary(experiment.metric_comparisons[0]?.evidence_chain)}</p></> },
                { color: (job?.resource_adaptations.length ?? 0) ? "orange" : "gray", children: <><strong>Resource adaptation</strong><p>{job?.resource_adaptations.length ? `${job.resource_adaptations.length} recorded adaptation(s)` : "No adaptation recorded"}</p></> },
                { color: "green", children: <><strong>Canonical FinalResult</strong><p>{experiment.final_result_id ? compactId(experiment.final_result_id) : "Not acquired"}</p></> },
                { color: "green", children: <><strong>Comparison</strong><p>{experiment.metric_comparisons.length} metric comparison(s) · {humanize(experiment.status)}</p></> },
              ]} />
              {experiment.metric_comparisons.map((metric) => (
                <Card size="small" key={metric.comparison_id} title={metricName(metric)}>
                  <Descriptions column={1} size="small" colon={false}>
                    <Descriptions.Item label="Paper evidence">{chainCount(metric.evidence_chain?.paper_evidence)} item(s)</Descriptions.Item>
                    <Descriptions.Item label="Run evidence">{chainCount(metric.evidence_chain?.run_ids)} run(s)</Descriptions.Item>
                    <Descriptions.Item label="Status">{humanize(metric.status)}</Descriptions.Item>
                    <Descriptions.Item label="Reason">{metric.reason ?? "—"}</Descriptions.Item>
                  </Descriptions>
                </Card>
              ))}
            </div>
          ),
        }))}
      />
    </div>
  );
}

function chainCount(value: unknown): number { return Array.isArray(value) ? value.length : 0; }

function evidenceSummary(chain?: Record<string, unknown>): string {
  if (!chain) return "Evidence not yet available";
  const checkpoints = Array.isArray(chain.checkpoint_references) ? chain.checkpoint_references : [];
  const epochs = Array.isArray(chain.selected_epochs) ? chain.selected_epochs : [];
  const seeds = Array.isArray(chain.seeds) ? chain.seeds : [];
  return `${checkpoints.length} checkpoint reference(s) · epochs ${epochs.length ? epochs.join(", ") : "—"} · seeds ${seeds.length ? seeds.join(", ") : "—"}`;
}

export function Inspector(props: Props) {
  const items = [
    { key: "overview", label: <span><AimOutlined /> Overview</span>, children: <Overview intake={props.intake} job={props.job} /> },
    { key: "experiments", label: <span><ExperimentOutlined /> Experiments</span>, children: <Experiments intake={props.intake} job={props.job} events={props.events} /> },
    { key: "execution", label: <span><ApartmentOutlined /> Execution</span>, children: <Execution job={props.job} events={props.events} /> },
    { key: "resources", label: <span><DatabaseOutlined /> Resources</span>, children: <Resources intake={props.intake} job={props.job} events={props.events} /> },
    { key: "results", label: <span><FileDoneOutlined /> Results</span>, children: <Results results={props.results} comparison={props.comparison} loading={props.resultsLoading} /> },
    { key: "evidence", label: <span><FileSearchOutlined /> Evidence</span>, children: <Evidence comparison={props.comparison} results={props.results} job={props.job} /> },
  ];
  return (
    <aside className="inspector-panel" aria-label="Task inspector">
      <div className="panel-heading inspector-heading"><div><span className="eyebrow">Task inspector</span><h2>Scientific trace</h2></div></div>
      <Tabs className="inspector-tabs" defaultActiveKey="overview" items={items} />
    </aside>
  );
}
