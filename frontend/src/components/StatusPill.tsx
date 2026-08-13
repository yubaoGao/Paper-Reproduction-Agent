import { Tag } from "antd";
import { humanize, statusTone } from "../utils/presentation";

const colors = {
  success: "green",
  processing: "blue",
  warning: "gold",
  error: "red",
  default: "default",
} as const;

export function StatusPill({ status }: { status?: string | null }) {
  const tone = statusTone(status ?? undefined);
  return <Tag color={colors[tone]} bordered={false}>{humanize(status)}</Tag>;
}
