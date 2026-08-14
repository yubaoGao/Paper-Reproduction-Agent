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
        <span>整体状态</span>
        <div><StatusPill status={job?.state ?? intake?.state} /></div>
        <p>{job?.current_action ? humanize(job.current_action) : intake?.waiting_reason ?? "等待进入下一阶段"}</p>
      </div>
      <div className="overview-progress">
        <div><span>进度</span><strong>{completed} / {total}</strong></div>
        <Progress percent={total ? Math.round(completed / total * 100) : 0} showInfo={false} />
      </div>
      <Descriptions column={1} size="small" colon={false} className="detail-descriptions">
        <Descriptions.Item label="已选实验">{selected.length}</Descriptions.Item>
        <Descriptions.Item label="队列状态">{humanize(job?.state ?? intake?.state)}</Descriptions.Item>
        <Descriptions.Item label="当前操作">{humanize(job?.current_action)}</Descriptions.Item>
        <Descriptions.Item label="已用时间">{elapsed(job?.started_at, job?.finished_at)}</Descriptions.Item>
        <Descriptions.Item label="开始时间">{formatTime(job?.started_at)}</Descriptions.Item>
        <Descriptions.Item label="结束时间">{formatTime(job?.finished_at)}</Descriptions.Item>
      </Descriptions>
      {job?.terminal_failure && <Alert type="error" showIcon message="任务最终失败" description={job.terminal_failure} />}
      {intake?.planning_blockers?.map((blocker, index) => (
        <Alert key={index} type="warning" showIcon message={String(blocker.code ?? "规划受阻")} description={String(blocker.message ?? "需要处理")} />
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
      <p className="section-intro">此处仅显示已由目标解析器确定的实验。</p>
      {selected.length ? selected.map((id, index) => (
        <Card size="small" className="experiment-card" key={id}>
          <div className="experiment-number">{String(index + 1).padStart(2, "0")}</div>
          <div className="experiment-info"><strong>{id}</strong><span>已选实验</span></div>
          <StatusPill status={statusByExperiment.get(id) ?? "selected"} />
        </Card>
      )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="实验范围尚未确定" />}
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
  const impact = stringValue(adaptation.impact ?? adaptation.semantic_impact) ?? "未知";
  const controlled = impact.toLowerCase() === "controlled_deviation";
  return (
    <Card size="small" className={`adaptation-card ${controlled ? "controlled" : ""}`}>
      <div className="adaptation-heading"><WarningOutlined /><strong>资源调整</strong><Tag color={controlled ? "gold" : "green"}>{humanize(impact)}</Tag></div>
      {controlled && <Alert type="warning" showIcon message="受控科研偏差" description="此调整可能影响复现语义，请在解读结果前检查相关证据。" />}
      <div className="adaptation-grid">
        <div><span>原配置</span>{Object.entries(original).slice(0, 6).map(([key, value]) => <code key={key}>{key} = {String(value)}</code>)}</div>
        <div className="adaptation-arrow">→</div>
        <div><span>调整后</span>{Object.entries(adapted).slice(0, 6).map(([key, value]) => <code key={key}>{key} = {String(value)}</code>)}</div>
      </div>
      {(adaptation.effective_batch_before !== undefined || adaptation.effective_batch_after !== undefined) && <p>有效批大小 · <strong>{String(adaptation.effective_batch_before ?? "—")} → {String(adaptation.effective_batch_after ?? "—")}</strong></p>}
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
          <div className="epoch-heading"><span>实时训练进度</span><strong>轮次 {currentEpoch ?? "—"} / {totalEpochs ?? "—"}</strong></div>
          <Progress percent={currentEpoch && totalEpochs ? Math.min(100, Math.round(currentEpoch / totalEpochs * 100)) : 0} status="active" />
          <div className="epoch-facts">
            {stringValue(epoch.payload.metric_name) && <span>当前指标 · {String(epoch.payload.metric_name)} = {String(epoch.payload.metric_value ?? "—")}</span>}
            {epoch.payload.best_epoch !== undefined && <span>最佳检查点 · 第 {String(epoch.payload.best_epoch)} 轮</span>}
            {epoch.payload.best_metric !== undefined && <span>最佳选择指标 · {String(epoch.payload.best_metric)}</span>}
          </div>
          <Typography.Text type="secondary">实时指标仅为过程信号，并非规范化最终结果。</Typography.Text>
        </Card>
      )}
      <div className="dag-label"><ApartmentOutlined /> 操作流程图</div>
      {steps.length ? (
        <div className="execution-dag">
          {steps.map((step, index) => (
            <div className="dag-node-wrap" key={step.id}>
              <div className={`dag-node ${step.status}`}>
                <span className="dag-dot" />
                <div><strong>{humanize(step.label)}</strong><small>{humanize(step.status)} · 已尝试 {step.attempts} 次</small></div>
              </div>
              {index < steps.length - 1 && <div className="dag-connector">↓</div>}
            </div>
          ))}
        </div>
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="工作器启动后将在此显示执行步骤" />}
      {oom && <Alert type="error" showIcon message="GPU 显存不足" description="资源助手正在评估受限的调整方案。" />}
      {adaptations.map((adaptation, index) => <AdaptationCard adaptation={adaptation} key={String(adaptation.adaptation_id ?? index)} />)}
      <Collapse
        ghost
        items={[{
          key: "logs",
          label: <span><HistoryOutlined /> 查看运行日志</span>,
          children: runtimeLines.length
            ? <pre className="runtime-log">{runtimeLines.join("\n")}</pre>
            : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无受控运行日志事件" />,
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
        <div className="gpu-summary-heading"><CloudServerOutlined /><strong>当前任务 GPU</strong>{waiting ? <Badge status="warning" text="等待中" /> : allocation ? <Badge status="processing" text="已分配" /> : <Badge status="default" text="后端管理" />}</div>
        <Descriptions column={1} size="small" colon={false}>
          <Descriptions.Item label="资源需求">{job?.gpu_requirement ? "由执行计划声明" : "未报告"}</Descriptions.Item>
          <Descriptions.Item label="资源分配">{allocation ? String(allocation.device_ids ?? allocation.allocated_gpu_ids ?? allocation.gpu_ids ?? "已分配") : "无"}</Descriptions.Item>
          <Descriptions.Item label="调度状态">{waiting ? String(waiting.payload.reason ?? "等待 GPU") : "当前无需等待"}</Descriptions.Item>
        </Descriptions>
        <Typography.Text type="secondary">此处仅显示与当前复现任务关联的资源。</Typography.Text>
      </Card>
      <div className="section-title"><DatabaseOutlined /> 外部资源</div>
      {resources.length ? resources.map((resource) => (
        <Card size="small" key={resource.requirement_id} className="inspector-resource-card">
          <div><strong>{resource.resource_name}</strong><span>{humanize(resource.resource_type)}</span></div>
          <StatusPill status={resource.status} />
        </Card>
      )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无外部资源" />}
      {(job?.resource_adaptations.length ?? 0) > 0 && <Alert type="info" showIcon message={`已记录 ${job?.resource_adaptations.length} 次资源调整`} />}
    </div>
  );
}

function metricName(item: MetricComparison): string {
  return item.paper_metric?.original_name ?? item.reproduced_metric?.original_name ?? item.paper_metric?.normalized_name ?? item.reproduced_metric?.normalized_name ?? "未命名指标";
}

function valueCell(value?: number | null): string {
  return value === null || value === undefined ? "—" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function Results({ results, comparison, loading }: { results?: FinalResult[]; comparison?: ComparisonReport; loading: boolean }) {
  const columns: ColumnsType<MetricComparison> = [
    { title: "指标", render: (_, item) => <strong>{metricName(item)}</strong> },
    { title: "论文值", dataIndex: "paper_value", render: valueCell },
    { title: "复现值", dataIndex: "reproduced_value", render: valueCell },
    { title: "差异", dataIndex: "absolute_difference", render: valueCell },
    { title: "状态", dataIndex: "status", render: (status: string) => <StatusPill status={status} /> },
  ];
  const compared = comparison?.experiments.flatMap((experiment) => experiment.metric_comparisons) ?? [];
  const additional = comparison?.experiments.flatMap((experiment) => experiment.additional_metrics ?? []) ?? [];
  const resultMetrics = results?.flatMap((result) => result.reporting_metrics) ?? [];
  const missing = resultMetrics.filter((metric) => metric.status !== "available");
  return (
    <div className="inspector-section results-section">
      {loading && <Card loading />}
      {!loading && compared.length > 0 && <Table rowKey="comparison_id" size="small" pagination={false} scroll={{ x: 540 }} columns={columns} dataSource={compared} />}
      {!loading && !compared.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="规范化结果生成后可查看对比" />}
      {missing.map((metric) => <MetricStatusCard metric={metric} key={`${metric.name}-${metric.split}`} />)}
      {additional.length > 0 && (
        <div className="additional-metrics">
          <h4>其他复现指标</h4>
          <p>论文中未声明的仓库指标会单独展示，不会被虚构为对比项。</p>
          {additional.map((metric) => <MetricStatusCard metric={metric} key={`${metric.name}-${metric.split}`} />)}
        </div>
      )}
    </div>
  );
}

function MetricStatusCard({ metric }: { metric: FinalMetric }) {
  return (
    <div className="metric-status-card">
      <div><strong>{metric.name}</strong><span>{metric.split ?? "报告数据划分"}</span></div>
      <div className="metric-value">{metric.status === "available" ? valueCell(metric.value) : "—"}</div>
      <StatusPill status={metric.status} />
    </div>
  );
}

function Evidence({ comparison, results, job }: Pick<Props, "comparison" | "results" | "job">) {
  if (!comparison) return <div className="inspector-section"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="生成对比报告后将在此显示证据链" /></div>;
  return (
    <div className="inspector-section evidence-section">
      <Alert type="info" showIcon message="可追溯的科研证据" description="每条证据链都会把已选实验和论文结论关联到准确的复现结果，且不会展示模型的私有推理过程。" />
      <Collapse
        accordion
        items={comparison.experiments.map((experiment) => ({
          key: experiment.paper_experiment_id,
          label: <div className="evidence-collapse-label"><FileSearchOutlined /><span>{experiment.paper_experiment_id}</span><StatusPill status={experiment.status} /></div>,
          children: (
            <div className="evidence-chain">
              <Timeline items={[
                { color: "blue", children: <><strong>已选实验</strong><p>{experiment.paper_experiment_id}</p></> },
                { color: "blue", children: <><strong>评估策略</strong><p>{humanize(comparison.selection_mode)}选择 · {results?.find((result) => result.paper_experiment_id === experiment.paper_experiment_id)?.aggregation ?? "后端定义的聚合方式"}</p></> },
                { color: "blue", children: <><strong>检查点、轮次与随机种子</strong><p>{evidenceSummary(experiment.metric_comparisons[0]?.evidence_chain)}</p></> },
                { color: (job?.resource_adaptations.length ?? 0) ? "orange" : "gray", children: <><strong>资源调整</strong><p>{job?.resource_adaptations.length ? `已记录 ${job.resource_adaptations.length} 次调整` : "未记录资源调整"}</p></> },
                { color: "green", children: <><strong>规范化最终结果</strong><p>{experiment.final_result_id ? compactId(experiment.final_result_id) : "尚未获取"}</p></> },
                { color: "green", children: <><strong>结果对比</strong><p>{experiment.metric_comparisons.length} 项指标对比 · {humanize(experiment.status)}</p></> },
              ]} />
              {experiment.metric_comparisons.map((metric) => (
                <Card size="small" key={metric.comparison_id} title={metricName(metric)}>
                  <Descriptions column={1} size="small" colon={false}>
                    <Descriptions.Item label="论文证据">{chainCount(metric.evidence_chain?.paper_evidence)} 项</Descriptions.Item>
                    <Descriptions.Item label="运行证据">{chainCount(metric.evidence_chain?.run_ids)} 次运行</Descriptions.Item>
                    <Descriptions.Item label="状态">{humanize(metric.status)}</Descriptions.Item>
                    <Descriptions.Item label="原因">{metric.reason ?? "—"}</Descriptions.Item>
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
  if (!chain) return "证据尚不可用";
  const checkpoints = Array.isArray(chain.checkpoint_references) ? chain.checkpoint_references : [];
  const epochs = Array.isArray(chain.selected_epochs) ? chain.selected_epochs : [];
  const seeds = Array.isArray(chain.seeds) ? chain.seeds : [];
  return `${checkpoints.length} 个检查点引用 · 轮次 ${epochs.length ? epochs.join(", ") : "—"} · 随机种子 ${seeds.length ? seeds.join(", ") : "—"}`;
}

export function Inspector(props: Props) {
  const items = [
    { key: "overview", label: <span><AimOutlined /> 概览</span>, children: <Overview intake={props.intake} job={props.job} /> },
    { key: "experiments", label: <span><ExperimentOutlined /> 实验</span>, children: <Experiments intake={props.intake} job={props.job} events={props.events} /> },
    { key: "execution", label: <span><ApartmentOutlined /> 执行</span>, children: <Execution job={props.job} events={props.events} /> },
    { key: "resources", label: <span><DatabaseOutlined /> 资源</span>, children: <Resources intake={props.intake} job={props.job} events={props.events} /> },
    { key: "results", label: <span><FileDoneOutlined /> 结果</span>, children: <Results results={props.results} comparison={props.comparison} loading={props.resultsLoading} /> },
    { key: "evidence", label: <span><FileSearchOutlined /> 证据</span>, children: <Evidence comparison={props.comparison} results={props.results} job={props.job} /> },
  ];
  return (
    <aside className="inspector-panel" aria-label="任务详情">
      <div className="panel-heading inspector-heading"><div><span className="eyebrow">任务详情</span><h2>科研追踪</h2></div></div>
      <Tabs className="inspector-tabs" defaultActiveKey="overview" items={items} />
    </aside>
  );
}
