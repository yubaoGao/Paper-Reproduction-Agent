import { useMemo, useState } from "react";
import {
  CheckCircleOutlined, DatabaseOutlined, ExperimentOutlined,
  LoadingOutlined, PlayCircleOutlined, QuestionCircleOutlined,
  SendOutlined, StopOutlined,
} from "@ant-design/icons";
import { Alert, Button, Card, Divider, Input, List, Progress, Space, Spin, Tag, Typography } from "antd";
import type {
  Intake, JobDetail, ProductEvent, ReproductionSession, ResourceRequirement, SessionExperiment,
} from "../api/types";
import { humanize, isTerminal, numberValue } from "../utils/presentation";
import { NewReproductionForm } from "./NewReproductionForm";
import { ProductEventTimeline } from "./ProductEventTimeline";
import { StatusPill } from "./StatusPill";

interface ClarificationTarget {
  candidate_experiment_ids: string[];
  clarification_questions: string[];
}

interface ReadyTarget {
  selected_experiment_ids: string[];
  required_resources: ResourceRequirement[];
  planning_status?: string | null;
}

interface ResourceTarget {
  required_resources: ResourceRequirement[];
}

interface Props {
  intake?: Intake;
  session?: ReproductionSession;
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
  onAppendGoal?: (goal: string) => void;
  onRunExperiment?: (experimentId: string) => void;
}

function UserPrompt({ intake, session, job }: { intake?: Intake; session?: ReproductionSession; job?: JobDetail }) {
  const goal = session?.goal ?? intake?.goal ?? job?.goal;
  const repository = session?.repository_url ?? intake?.repository_url;
  if (!goal) return null;
  return (
    <div className="message-row user-message-row">
      <div className="message user-message">
        {repository && <div className="message-context">代码仓库 · {repository}</div>}
        <div>{goal}</div>
      </div>
    </div>
  );
}

function AnalyzingCard({ phase }: { phase?: string | null }) {
  const steps = [
    { id: "paper_parsing", label: "解析论文" },
    { id: "paper_extracting", label: "提取实验" },
    { id: "goal_resolving", label: "解析用户复现目标" },
    { id: "repository_analyzing", label: "分析代码仓库" },
    { id: "aligning", label: "论文代码对齐" },
  ] as const;
  const rank: Record<string, number> = {
    pending: 0,
    paper_parsing: 1,
    paper_extracting: 2,
    goal_resolving: 3,
    waiting_for_clarification: 3,
    repository_analyzing: 4,
    aligning: 5,
    preparing: 6,
    ready_to_run: 6,
    failed: 0,
  };
  const current = rank[phase ?? "pending"] ?? 0;
  const stoppedEarly = phase === "waiting_for_clarification";
  return (
    <div className="assistant-message analyzing-message">
      <Spin indicator={<LoadingOutlined spin />} />
      <div>
        <strong>正在分析您的复现请求</strong>
        <p>正在按阶段处理。代码仓库分析仅在实验范围明确之后才会开始。</p>
        <ol className="analysis-phase-list">
          {steps.map((step, index) => {
            const stepRank = index + 1;
            const skipped = stoppedEarly && stepRank > 3;
            const done = !skipped && current > stepRank;
            const active = !skipped && current === stepRank;
            const mark = skipped ? "○" : done ? "✓" : active ? "●" : "○";
            return (
              <li key={step.id} className={active ? "active" : done ? "done" : skipped ? "skipped" : undefined}>
                <span className="phase-mark">{mark}</span>
                {step.label}
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

function AmbiguousFlow({ target, loading, onClarify }: { target: ClarificationTarget; loading: boolean; onClarify: (answers: string[]) => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [answer, setAnswer] = useState("");
  const submit = () => {
    const values = [...selected.map((id) => `选择实验 ${id}`), answer.trim()].filter(Boolean);
    if (values.length) onClarify(values);
  };
  return (
    <div className="assistant-message flow-message">
      <div className="assistant-label"><QuestionCircleOutlined /> 目标解析器</div>
      <h3>发现了多个可能的实验，请确认要复现的范围。</h3>
      <div className="candidate-grid">
        {target.candidate_experiment_ids.map((id) => {
          const active = selected.includes(id);
          return (
            <button
              key={id}
              className={`candidate-card ${active ? "selected" : ""}`}
              onClick={() => setSelected((items) => active ? items.filter((item) => item !== id) : [...items, id])}
            >
              <ExperimentOutlined />
              <div><strong>{id}</strong><span>候选实验</span></div>
              {active && <CheckCircleOutlined />}
            </button>
          );
        })}
      </div>
      {target.clarification_questions.map((question) => <p className="clarification-question" key={question}>{question}</p>)}
      <Input.TextArea value={answer} onChange={(event) => setAnswer(event.target.value)} rows={3} placeholder="补充说明、约束条件或准确的实验名称……" />
      <Button type="primary" icon={<SendOutlined />} loading={loading} disabled={!selected.length && !answer.trim()} onClick={submit}>继续分析</Button>
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
      {!!resource.source_urls.length && <div className="source-links">{resource.source_urls.map((url) => <a href={url} target="_blank" rel="noreferrer" key={url}>官方来源</a>)}</div>}
      {!!resource.expected_structure.length && (
        <div className="expected-structure"><span>预期目录结构</span><code>{resource.expected_structure.join("\n")}</code></div>
      )}
      <Alert type="info" showIcon message="请自行下载并准备此资源，然后提供已授权的服务器目录。" />
      <Space.Compact block>
        <Input value={path} onChange={(event) => setPath(event.target.value)} placeholder="已授权的服务器目录" aria-label={`${resource.resource_name} 的路径`} />
        <Button type="primary" loading={loading} disabled={!path.trim()} onClick={() => onSubmit(path.trim())}>验证路径</Button>
      </Space.Compact>
    </Card>
  );
}

function MissingResourceFlow({ target, loading, onResource }: { target: ResourceTarget; loading: boolean; onResource: Props["onResource"] }) {
  const missing = target.required_resources.filter((resource) => resource.required && resource.status !== "available");
  return (
    <div className="assistant-message flow-message wide-message">
      <div className="assistant-label warning"><DatabaseOutlined /> 资源助手</div>
      <h3>继续规划前需要补充外部资源。</h3>
      <p>ReproPilot 只会验证您提供的路径，不会扫描服务器或自动下载数据。</p>
      <div className="resource-request-list">
        {missing.map((resource) => <ResourceCard key={resource.requirement_id} resource={resource} loading={loading} onSubmit={(path) => onResource(resource.requirement_id, path)} />)}
      </div>
    </div>
  );
}

function ReadyFlow({ target, loading, onStart }: { target: ReadyTarget; loading: boolean; onStart: () => void }) {
  const available = target.required_resources.filter((resource) => resource.status === "available").length;
  return (
    <div className="assistant-message flow-message ready-message">
      <div className="assistant-label success"><CheckCircleOutlined /> 规划器</div>
      <h3>复现计划已准备就绪。</h3>
      <div className="ready-summary">
        <div><span>已选实验</span><strong>{target.selected_experiment_ids.length}</strong></div>
        <div><span>资源就绪</span><strong>{available} / {target.required_resources.length}</strong></div>
        <div><span>规划状态</span><strong>{humanize(target.planning_status ?? "ready")}</strong></div>
      </div>
      <div className="selected-tags">{target.selected_experiment_ids.map((id) => <Tag key={id}>{id}</Tag>)}</div>
      <Button size="large" type="primary" icon={<PlayCircleOutlined />} loading={loading} onClick={onStart}>开始复现</Button>
      <small>点击开始后，任务才会进入持久化执行队列。</small>
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
      {job.current_action && <p>当前操作 · <strong>{humanize(job.current_action)}</strong></p>}
      {job.waiting_reason && <Alert type="warning" showIcon message={job.waiting_reason} />}
      <Progress percent={percent} status={job.state === "failed" ? "exception" : job.state === "succeeded" ? "success" : "active"} />
      {job.terminal_failure && <Alert type="error" showIcon message="执行失败" description={job.terminal_failure} />}
      {["ready", "queued", "claimed", "running"].includes(job.state) && <Button danger icon={<StopOutlined />} loading={loading} onClick={onCancel}>取消复现</Button>}
    </div>
  );
}

function ExperimentCatalog({ experiments, loading, onRun }: { experiments: SessionExperiment[]; loading: boolean; onRun?: (experimentId: string) => void }) {
  if (!experiments.length) return null;
  return (
    <div className="assistant-message flow-message experiment-catalog">
      <div className="assistant-label"><ExperimentOutlined /> 实验目录</div>
      <h3>当前论文实验状态</h3>
      <div className="session-experiment-list">
        {experiments.map((experiment) => (
          <div className="session-experiment-row" key={experiment.experiment_id}>
            <div>
              <strong>{experiment.experiment_id}</strong>
              <span>{experiment.name}</span>
              {experiment.job_history.length > 1 && (
                <small className="run-history">
                  运行历史 · {experiment.job_history.map((item) => `${item.job_id.split(":").at(-1)?.slice(0, 6)} ${humanize(item.status)}`).join(" / ")}
                </small>
              )}
            </div>
            <div className="session-experiment-actions">
              <StatusPill status={experiment.status} />
              {experiment.status === "not_selected" && onRun && (
                <Button size="small" icon={<PlayCircleOutlined />} loading={loading} onClick={() => onRun(experiment.experiment_id)}>复现</Button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SessionComposer({ disabled, loading, onSubmit }: { disabled: boolean; loading: boolean; onSubmit: (goal: string) => void }) {
  const [goal, setGoal] = useState("");
  const submit = () => {
    const value = goal.trim();
    if (!value) return;
    onSubmit(value);
    setGoal("");
  };
  return (
    <div className="session-composer" data-testid="session-composer">
      <Input.TextArea
        value={goal}
        onChange={(event) => setGoal(event.target.value)}
        rows={3}
        disabled={disabled}
        placeholder="继续复现其他实验，例如：继续复现消融实验 B"
        onPressEnter={(event) => {
          if (!event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <Button type="primary" icon={<SendOutlined />} loading={loading} disabled={disabled || !goal.trim()} onClick={submit}>追加实验</Button>
    </div>
  );
}

export function ConversationWorkspace(props: Props) {
  const { intake, session, job, events, creating, actionLoading, error, streamStatus } = props;
  const latestEpoch = useMemo(() => [...events].reverse().find((event) => event.type === "EPOCH_PROGRESS"), [events]);
  const clarifying = session?.status === "awaiting_clarification" || intake?.state === "ambiguous";
  const waitingResource = session?.status === "waiting_for_resource" || intake?.state === "waiting_for_resource";
  const readyToRun = Boolean(session?.pending_job_id) || intake?.state === "ready_to_run";
  const failed = intake?.state === "failed";
  const analyzing = creating || intake?.state === "analyzing";
  const canAppend = Boolean(session) && !analyzing && !clarifying && !waitingResource && !failed;
  if (!intake && !job && !session && !creating) {
    return <main className="conversation-panel"><NewReproductionForm loading={creating} error={error} onSubmit={props.onCreate} /></main>;
  }
  return (
    <main className="conversation-panel">
      <header className="conversation-header">
        <div><span className="eyebrow">复现工作区</span><h2>{session?.goal ?? intake?.goal ?? job?.goal ?? "正在分析复现任务"}</h2></div>
        <div className="conversation-status"><StatusPill status={session?.status ?? intake?.state ?? job?.state ?? "analyzing"} />{job && <span className={`stream-state ${streamStatus}`}>SSE · {humanize(streamStatus)}</span>}</div>
      </header>
      <div className="conversation-scroll">
        <UserPrompt intake={intake} session={session} job={job} />
        {analyzing && <AnalyzingCard phase={intake?.current_phase} />}
        {failed && (
          <Alert
            type="error"
            showIcon
            message={intake?.error_code ? `分析失败 · ${intake.error_code}` : "分析失败"}
            description={intake?.error_message ?? intake?.waiting_reason ?? "Intake 分析未能完成。"}
          />
        )}
        {error && <Alert type="error" showIcon message="ReproPilot 无法继续执行" description={error} closable />}
        {session && <ExperimentCatalog experiments={session.experiments} loading={actionLoading} onRun={props.onRunExperiment} />}
        <ProductEventTimeline events={events} />
        {latestEpoch && (
          <div className="live-epoch-strip">
            <span>实时训练</span>
            <strong>轮次 {String(latestEpoch.payload.epoch ?? latestEpoch.payload.current_epoch ?? "—")} / {String(latestEpoch.payload.total_epochs ?? "—")}</strong>
            <Typography.Text type="secondary">过程指标仅为实时信号，并非最终结果。</Typography.Text>
          </div>
        )}
        {clarifying && (
          <AmbiguousFlow
            target={session ?? intake!}
            loading={actionLoading}
            onClarify={props.onClarify}
          />
        )}
        {waitingResource && (
          <MissingResourceFlow
            target={session ?? intake!}
            loading={actionLoading}
            onResource={props.onResource}
          />
        )}
        {readyToRun && !clarifying && !waitingResource && (
          <ReadyFlow
            target={session ?? intake!}
            loading={actionLoading}
            onStart={props.onStart}
          />
        )}
        {job && <JobStateMessage job={job} loading={actionLoading} onCancel={props.onCancel} />}
        {job && isTerminal(job.state) && !session && <Divider plain>当前活动已结束</Divider>}
      </div>
      {session && props.onAppendGoal && (
        <SessionComposer disabled={!canAppend} loading={actionLoading} onSubmit={props.onAppendGoal} />
      )}
    </main>
  );
}
