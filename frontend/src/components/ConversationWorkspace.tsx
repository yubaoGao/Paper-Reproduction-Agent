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
        {intake?.repository_url && <div className="message-context">代码仓库 · {intake.repository_url}</div>}
        <div>{goal}</div>
      </div>
    </div>
  );
}

function AnalyzingCard() {
  return (
    <div className="assistant-message analyzing-message">
      <Spin indicator={<LoadingOutlined spin />} />
      <div><strong>正在分析您的复现请求</strong><p>正在阅读论文、梳理仓库实现并确定实验范围。</p></div>
    </div>
  );
}

function AmbiguousFlow({ intake, loading, onClarify }: { intake: Intake; loading: boolean; onClarify: (answers: string[]) => void }) {
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
        {intake.candidate_experiment_ids.map((id) => {
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
      {intake.clarification_questions.map((question) => <p className="clarification-question" key={question}>{question}</p>)}
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

function MissingResourceFlow({ intake, loading, onResource }: { intake: Intake; loading: boolean; onResource: Props["onResource"] }) {
  const missing = intake.required_resources.filter((resource) => resource.required && resource.status !== "available");
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

function ReadyFlow({ intake, loading, onStart }: { intake: Intake; loading: boolean; onStart: () => void }) {
  const available = intake.required_resources.filter((resource) => resource.status === "available").length;
  return (
    <div className="assistant-message flow-message ready-message">
      <div className="assistant-label success"><CheckCircleOutlined /> 规划器</div>
      <h3>复现计划已准备就绪。</h3>
      <div className="ready-summary">
        <div><span>已选实验</span><strong>{intake.selected_experiment_ids.length}</strong></div>
        <div><span>资源就绪</span><strong>{available} / {intake.required_resources.length}</strong></div>
        <div><span>规划状态</span><strong>{humanize(intake.planning_status ?? "ready")}</strong></div>
      </div>
      <div className="selected-tags">{intake.selected_experiment_ids.map((id) => <Tag key={id}>{id}</Tag>)}</div>
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

export function ConversationWorkspace(props: Props) {
  const { intake, job, events, loading, creating, actionLoading, error, streamStatus } = props;
  const latestEpoch = useMemo(() => [...events].reverse().find((event) => event.type === "EPOCH_PROGRESS"), [events]);
  if (!intake && !job && !creating) {
    return <main className="conversation-panel"><NewReproductionForm loading={creating} error={error} onSubmit={props.onCreate} /></main>;
  }
  return (
    <main className="conversation-panel">
      <header className="conversation-header">
        <div><span className="eyebrow">复现工作区</span><h2>{intake?.goal ?? job?.goal ?? "正在分析复现任务"}</h2></div>
        <div className="conversation-status"><StatusPill status={intake?.state ?? job?.state ?? "analyzing"} />{job && <span className={`stream-state ${streamStatus}`}>SSE · {humanize(streamStatus)}</span>}</div>
      </header>
      <div className="conversation-scroll">
        <UserPrompt intake={intake} job={job} />
        {(creating || loading || intake?.state === "analyzing") && <AnalyzingCard />}
        {error && <Alert type="error" showIcon message="ReproPilot 无法继续执行" description={error} closable />}
        <ProductEventTimeline events={events} />
        {latestEpoch && (
          <div className="live-epoch-strip">
            <span>实时训练</span>
            <strong>轮次 {String(latestEpoch.payload.epoch ?? latestEpoch.payload.current_epoch ?? "—")} / {String(latestEpoch.payload.total_epochs ?? "—")}</strong>
            <Typography.Text type="secondary">过程指标仅为实时信号，并非最终结果。</Typography.Text>
          </div>
        )}
        {intake?.state === "ambiguous" && <AmbiguousFlow intake={intake} loading={actionLoading} onClarify={props.onClarify} />}
        {intake?.state === "waiting_for_resource" && <MissingResourceFlow intake={intake} loading={actionLoading} onResource={props.onResource} />}
        {intake?.state === "ready_to_run" && <ReadyFlow intake={intake} loading={actionLoading} onStart={props.onStart} />}
        {job && <JobStateMessage job={job} loading={actionLoading} onCancel={props.onCancel} />}
        <Divider plain>当前活动已结束</Divider>
      </div>
    </main>
  );
}
