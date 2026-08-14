import {
  CheckCircleFilled, ClockCircleOutlined, ExperimentOutlined,
  FileSearchOutlined, LoadingOutlined, WarningFilled,
} from "@ant-design/icons";
import type { ProductEvent } from "../api/types";
import { formatTime, humanize, numberValue, stringValue } from "../utils/presentation";

const eventPresentation: Record<string, { actor: string; text: string; tone?: string }> = {
  PAPER_ANALYSIS_STARTED: { actor: "论文助手", text: "正在读取论文实验结构" },
  PAPER_ANALYSIS_COMPLETED: { actor: "论文助手", text: "论文实验分析已完成" },
  REPOSITORY_ANALYSIS_STARTED: { actor: "仓库助手", text: "正在检查仓库实现" },
  REPOSITORY_ANALYSIS_COMPLETED: { actor: "仓库助手", text: "仓库分析已完成" },
  EXPERIMENT_SELECTION_RESOLVED: { actor: "目标解析器", text: "实验选择已确定" },
  CLARIFICATION_REQUIRED: { actor: "目标解析器", text: "需要补充说明", tone: "warning" },
  RESOURCE_REQUIRED: { actor: "资源助手", text: "需要外部资源", tone: "warning" },
  RESOURCE_RESOLVED: { actor: "资源助手", text: "外部资源验证通过" },
  PLANNING_STARTED: { actor: "规划器", text: "正在制定复现计划" },
  PLANNING_COMPLETED: { actor: "规划器", text: "复现计划已生成" },
  JOB_QUEUED: { actor: "执行队列", text: "任务已进入持久化队列" },
  JOB_CLAIMED: { actor: "执行工作器", text: "任务已由工作器领取" },
  GPU_WAITING: { actor: "GPU 调度器", text: "正在等待合适的 GPU 资源", tone: "waiting" },
  GPU_ALLOCATED: { actor: "GPU 调度器", text: "GPU 资源已分配" },
  STEP_STARTED: { actor: "实验运行器", text: "实验步骤已开始", tone: "running" },
  STEP_COMPLETED: { actor: "实验运行器", text: "实验步骤已完成" },
  EPOCH_PROGRESS: { actor: "训练任务", text: "训练轮次进度已更新", tone: "running" },
  AGENT_PATCH_STARTED: { actor: "补丁助手", text: "正在准备受限补丁", tone: "running" },
  AGENT_PATCH_COMPLETED: { actor: "补丁助手", text: "受限补丁已完成" },
  GPU_OOM: { actor: "资源助手", text: "检测到 GPU 显存不足", tone: "error" },
  RESOURCE_ADAPTED: { actor: "资源助手", text: "执行资源已调整", tone: "warning" },
  STEP_RETRYING: { actor: "实验运行器", text: "正在重试此步骤", tone: "warning" },
  FINAL_RESULT_ACQUIRED: { actor: "结果解析器", text: "已获取规范化最终结果" },
  COMPARISON_COMPLETED: { actor: "结果比较器", text: "论文结果对比已完成" },
  JOB_SUCCEEDED: { actor: "ReproPilot", text: "复现成功" },
  JOB_FAILED: { actor: "ReproPilot", text: "复现失败", tone: "error" },
  JOB_CANCELLED: { actor: "ReproPilot", text: "复现已取消", tone: "warning" },
};

function detail(event: ProductEvent): string | undefined {
  const payload = event.payload;
  if (event.type === "EXPERIMENT_SELECTION_RESOLVED") {
    const ids = payload.selected_experiment_ids;
    if (Array.isArray(ids)) return `已选择 ${ids.length} 个实验`;
  }
  if (event.type === "EPOCH_PROGRESS") {
    const current = numberValue(payload.epoch ?? payload.current_epoch);
    const total = numberValue(payload.total_epochs);
    if (current !== undefined) return total ? `轮次 ${current} / ${total}` : `轮次 ${current}`;
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
    <section className="agent-timeline" aria-label="任务活动">
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
