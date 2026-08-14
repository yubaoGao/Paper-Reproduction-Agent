import dayjs from "dayjs";

const labels: Record<string, string> = {
  analyzing: "分析中",
  paper_parsing: "解析论文",
  paper_extracting: "提取实验",
  goal_resolving: "解析目标",
  waiting_for_clarification: "等待澄清",
  repository_analyzing: "分析仓库",
  aligning: "论文代码对齐",
  preparing: "准备计划",
  ambiguous: "待确认",
  waiting_for_resource: "等待资源",
  awaiting_clarification: "待确认",
  ready_to_run: "可运行",
  terminal: "已结束",
  active: "进行中",
  not_selected: "未选择",
  pending: "待处理",
  planning: "规划中",
  ready: "已就绪",
  queued: "排队中",
  claimed: "已领取",
  running: "运行中",
  succeeded: "已成功",
  failed: "失败",
  cancel_requested: "正在取消",
  cancelled: "已取消",
  available: "可用",
  completed: "已完成",
  missing: "缺失",
  unavailable: "不可用",
  invalid: "无效",
  selected: "已选择",
  waiting: "等待中",
  retrying: "重试中",
  connected: "已连接",
  reconnecting: "正在重连",
  closed: "已断开",
  dataset: "数据集",
  checkpoint: "检查点",
  pretrained_model: "预训练模型",
  controlled_deviation: "受控偏差",
  exact: "精确匹配",
  matched: "已匹配",
  comparable: "可比较",
  not_comparable: "无法比较",
};

export function humanize(value?: string | null): string {
  if (!value) return "—";
  const normalized = value.toLowerCase();
  return labels[normalized] ?? value.replace(/_/g, " ");
}

export function formatTime(value?: string | null): string {
  return value ? dayjs(value).format("M月D日 HH:mm") : "—";
}

export function elapsed(start?: string | null, finish?: string | null): string {
  if (!start) return "尚未开始";
  const milliseconds = dayjs(finish ?? undefined).diff(dayjs(start));
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

export function compactId(value: string): string {
  const segment = value.includes(":") ? value.split(":").at(-1)! : value;
  return segment.length > 18 ? `${segment.slice(0, 8)}…${segment.slice(-6)}` : segment;
}

export function isTerminal(status?: string): boolean {
  return ["succeeded", "failed", "cancelled", "terminal"].includes(status ?? "");
}

export function statusTone(status?: string): "success" | "processing" | "warning" | "error" | "default" {
  if (["succeeded", "ready", "ready_to_run", "available", "completed", "active"].includes(status ?? "")) return "success";
  if (["running", "queued", "claimed", "analyzing", "planning", "paper_parsing", "paper_extracting", "goal_resolving", "repository_analyzing", "aligning", "preparing"].includes(status ?? "")) return "processing";
  if (["waiting_for_resource", "awaiting_clarification", "ambiguous", "cancel_requested", "missing", "unavailable", "not_selected"].includes(status ?? "")) return "warning";
  if (["failed", "cancelled", "invalid"].includes(status ?? "")) return "error";
  return "default";
}

export function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}
