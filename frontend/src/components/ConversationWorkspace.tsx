import { useMemo, useState } from "react";
import {
  CheckCircleOutlined, DatabaseOutlined, ExperimentOutlined,
  LoadingOutlined, PlayCircleOutlined, QuestionCircleOutlined,
  SendOutlined, StopOutlined,
} from "@ant-design/icons";
import { Alert, Button, Card, Divider, Input, List, Progress, Space, Spin, Tag, Typography } from "antd";
import type { Intake, JobDetail, ProductEvent, ResourceRequirement } from "../api/types";
import { humanize, numberValue } from "../utils/presentation";
import { NewReproductionForm } from "./NewReproductionForm";
import { ProductEventTimeline } from "./ProductEventTimeline";
import { StatusPill } from "./StatusPill";

interface Props {
  intake?: Intake;
  job?: JobDetail;
  events: ProductEvent[];
  loading: boolean;
  creating: boolean;
  actionLoading: boolean;
  error?: string;
  streamStatus: string;
  onCreate: (input: { pdf: File; repositoryUrl: string; goal: string }) => void;
  onClarify: (answers: string[]) => void;
  onResource: (requirementId: string, hostPath: string) => void;
  onStart: () => void;
  onCancel: () => void;
}

function UserPrompt({ intake, job }: { intake?: Intake; job?: JobDetail }) {
  const goal = intake?.goal ?? job?.goal;
  if (!goal) return null;
  return (
    <div className="message-row user-message-row">
      <div className="message user-message">
        {intake?.repository_url && <div className="message-context">Repository · {intake.repository_url}</div>}
        <div>{goal}</div>
      </div>
    </div>
  );
}

function AnalyzingCard() {
  return (
    <div className="assistant-message analyzing-message">
      <Spin indicator={<LoadingOutlined spin />} />
      <div><strong>Analyzing your reproduction request</strong><p>Reading the paper, mapping repository implementations, and resolving the experiment scope.</p></div>
    </div>
  );
}

function AmbiguousFlow({ intake, loading, onClarify }: { intake: Intake; loading: boolean; onClarify: (answers: string[]) => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [answer, setAnswer] = useState("");
  const submit = () => {
    const values = [...selected.map((id) => `Select experiment ${id}`), answer.trim()].filter(Boolean);
    if (values.length) onClarify(values);
  };
  return (
    <div className="assistant-message flow-message">
      <div className="assistant-label"><QuestionCircleOutlined /> Goal Resolver</div>
      <h3>I found several possible experiments. Please confirm the intended scope.</h3>
      <div className="candidate-grid">
        {intake.candidate_experiment_ids.map((id) => {
          const active = selected.includes(id);
          return (
            <button
              key={id}
              className={`candidate-card ${active ? "selected" : ""}`}
              onClick={() => setSelected((items) => active ? items.filter((item) => item !== id) : [...items, id])}
            >
              <ExperimentOutlined />
              <div><strong>{id}</strong><span>Candidate experiment</span></div>
              {active && <CheckCircleOutlined />}
            </button>
          );
        })}
      </div>
      {intake.clarification_questions.map((question) => <p className="clarification-question" key={question}>{question}</p>)}
      <Input.TextArea value={answer} onChange={(event) => setAnswer(event.target.value)} rows={3} placeholder="Add a clarification, constraint, or exact experiment name…" />
      <Button type="primary" icon={<SendOutlined />} loading={loading} disabled={!selected.length && !answer.trim()} onClick={submit}>Continue analysis</Button>
    </div>
  );
}

function ResourceCard({ resource, loading, onSubmit }: { resource: ResourceRequirement; loading: boolean; onSubmit: (path: string) => void }) {
  const [path, setPath] = useState("");
  return (
    <Card size="small" className="resource-request-card">
      <div className="resource-title-row">
        <DatabaseOutlined />
        <div><strong>{resource.resource_name}</strong><span>{humanize(resource.resource_type)}</span></div>
        <StatusPill status={resource.status} />
      </div>
      {!!resource.preparation_hints.length && <List size="small" dataSource={resource.preparation_hints} renderItem={(item) => <List.Item>{item}</List.Item>} />}
      {!!resource.source_urls.length && <div className="source-links">{resource.source_urls.map((url) => <a href={url} target="_blank" rel="noreferrer" key={url}>Official source</a>)}</div>}
      {!!resource.expected_structure.length && (
        <div className="expected-structure"><span>Expected structure</span><code>{resource.expected_structure.join("\n")}</code></div>
      )}
      <Alert type="info" showIcon message="Download and prepare this resource yourself, then provide its authorized server directory." />
      <Space.Compact block>
        <Input value={path} onChange={(event) => setPath(event.target.value)} placeholder="Authorized server directory" aria-label={`Path for ${resource.resource_name}`} />
        <Button type="primary" loading={loading} disabled={!path.trim()} onClick={() => onSubmit(path.trim())}>Validate path</Button>
      </Space.Compact>
    </Card>
  );
}

function MissingResourceFlow({ intake, loading, onResource }: { intake: Intake; loading: boolean; onResource: Props["onResource"] }) {
  const missing = intake.required_resources.filter((resource) => resource.required && resource.status !== "available");
  return (
    <div className="assistant-message flow-message wide-message">
      <div className="assistant-label warning"><DatabaseOutlined /> Resource Agent</div>
      <h3>External resources are required before planning can continue.</h3>
      <p>ReproPilot will validate only the path you provide. It will not scan the server or download data automatically.</p>
      <div className="resource-request-list">
        {missing.map((resource) => <ResourceCard key={resource.requirement_id} resource={resource} loading={loading} onSubmit={(path) => onResource(resource.requirement_id, path)} />)}
      </div>
    </div>
  );
}

function ReadyFlow({ intake, loading, onStart }: { intake: Intake; loading: boolean; onStart: () => void }) {
  const available = intake.required_resources.filter((resource) => resource.status === "available").length;
  return (
    <div className="assistant-message flow-message ready-message">
      <div className="assistant-label success"><CheckCircleOutlined /> Planner</div>
      <h3>The reproduction plan is ready.</h3>
      <div className="ready-summary">
        <div><span>Selected experiments</span><strong>{intake.selected_experiment_ids.length}</strong></div>
        <div><span>Resources ready</span><strong>{available} / {intake.required_resources.length}</strong></div>
        <div><span>Planning state</span><strong>{humanize(intake.planning_status ?? "ready")}</strong></div>
      </div>
      <div className="selected-tags">{intake.selected_experiment_ids.map((id) => <Tag key={id}>{id}</Tag>)}</div>
      <Button size="large" type="primary" icon={<PlayCircleOutlined />} loading={loading} onClick={onStart}>Start reproduction</Button>
      <small>Execution enters the durable queue only after you start it.</small>
    </div>
  );
}

function JobStateMessage({ job, loading, onCancel }: { job: JobDetail; loading: boolean; onCancel: () => void }) {
  const completed = numberValue(job.progress.completed_steps) ?? 0;
  const total = numberValue(job.progress.total_steps) ?? 0;
  const percent = total ? Math.round(completed / total * 100) : job.state === "succeeded" ? 100 : 0;
  return (
    <div className="assistant-message flow-message job-state-message">
      <div className="assistant-label"><ExperimentOutlined /> ReproPilot</div>
      <div className="job-state-heading"><h3>{humanize(job.state)}</h3><StatusPill status={job.state} /></div>
      {job.current_action && <p>Current action · <strong>{humanize(job.current_action)}</strong></p>}
      {job.waiting_reason && <Alert type="warning" showIcon message={job.waiting_reason} />}
      <Progress percent={percent} status={job.state === "failed" ? "exception" : job.state === "succeeded" ? "success" : "active"} />
      {job.terminal_failure && <Alert type="error" showIcon message="Execution failed" description={job.terminal_failure} />}
      {["ready", "queued", "claimed", "running"].includes(job.state) && <Button danger icon={<StopOutlined />} loading={loading} onClick={onCancel}>Cancel reproduction</Button>}
    </div>
  );
}

export function ConversationWorkspace(props: Props) {
  const { intake, job, events, loading, creating, actionLoading, error, streamStatus } = props;
  const latestEpoch = useMemo(() => [...events].reverse().find((event) => event.type === "EPOCH_PROGRESS"), [events]);
  if (!intake && !job && !creating) {
    return <main className="conversation-panel"><NewReproductionForm loading={creating} error={error} onSubmit={props.onCreate} /></main>;
  }
  return (
    <main className="conversation-panel">
      <header className="conversation-header">
        <div><span className="eyebrow">Conversation workspace</span><h2>{intake?.goal ?? job?.goal ?? "Analyzing reproduction"}</h2></div>
        <div className="conversation-status"><StatusPill status={intake?.state ?? job?.state ?? "analyzing"} />{job && <span className={`stream-state ${streamStatus}`}>SSE · {humanize(streamStatus)}</span>}</div>
      </header>
      <div className="conversation-scroll">
        <UserPrompt intake={intake} job={job} />
        {(creating || loading || intake?.state === "analyzing") && <AnalyzingCard />}
        {error && <Alert type="error" showIcon message="ReproPilot could not continue" description={error} closable />}
        <ProductEventTimeline events={events} />
        {latestEpoch && (
          <div className="live-epoch-strip">
            <span>Live training</span>
            <strong>Epoch {String(latestEpoch.payload.epoch ?? latestEpoch.payload.current_epoch ?? "—")} / {String(latestEpoch.payload.total_epochs ?? "—")}</strong>
            <Typography.Text type="secondary">Process metrics are live signals, not FinalResult.</Typography.Text>
          </div>
        )}
        {intake?.state === "ambiguous" && <AmbiguousFlow intake={intake} loading={actionLoading} onClarify={props.onClarify} />}
        {intake?.state === "waiting_for_resource" && <MissingResourceFlow intake={intake} loading={actionLoading} onResource={props.onResource} />}
        {intake?.state === "ready_to_run" && <ReadyFlow intake={intake} loading={actionLoading} onStart={props.onStart} />}
        {job && <JobStateMessage job={job} loading={actionLoading} onCancel={props.onCancel} />}
        <Divider plain>End of current activity</Divider>
      </div>
    </main>
  );
}
