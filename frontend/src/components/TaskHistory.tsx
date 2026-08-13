import { ClockCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Empty, Skeleton, Tooltip } from "antd";
import type { Intake, JobDetail } from "../api/types";
import { formatTime } from "../utils/presentation";
import { StatusPill } from "./StatusPill";

interface Props {
  jobs: JobDetail[];
  activeJobId?: string;
  activeIntake?: Intake;
  loading: boolean;
  onNew: () => void;
  onSelectJob: (jobId: string) => void;
  onSelectIntake: (intakeId: string) => void;
}

export function TaskHistory({ jobs, activeJobId, activeIntake, loading, onNew, onSelectJob, onSelectIntake }: Props) {
  return (
    <aside className="history-panel" aria-label="Reproduction sessions">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Workspace</span>
          <h2>Reproductions</h2>
        </div>
        <span className="history-count">{jobs.length}</span>
      </div>
      <Button type="primary" icon={<PlusOutlined />} block size="large" onClick={onNew}>
        New reproduction
      </Button>
      <div className="history-section-label">Recent sessions</div>
      <div className="history-list">
        {loading && <Skeleton active paragraph={{ rows: 5 }} title={false} />}
        {!loading && activeIntake && !jobs.some((job) => job.job_id === activeIntake.job_id) && (
          <button className="history-item active" onClick={() => onSelectIntake(activeIntake.intake_id)}>
            <div className="history-item-title">{activeIntake.goal}</div>
            <div className="history-item-meta"><StatusPill status={activeIntake.state} /><span><ClockCircleOutlined /> {formatTime(activeIntake.created_at)}</span></div>
          </button>
        )}
        {!loading && jobs.map((job) => (
          <button
            className={`history-item ${activeJobId === job.job_id ? "active" : ""}`}
            key={job.job_id}
            onClick={() => onSelectJob(job.job_id)}
          >
            <Tooltip title={job.goal} placement="right">
              <div className="history-item-title">{job.goal}</div>
            </Tooltip>
            <div className="history-item-meta">
              <StatusPill status={job.state} />
              <span><ClockCircleOutlined /> {formatTime(job.created_at)}</span>
            </div>
          </button>
        ))}
        {!loading && jobs.length === 0 && !activeIntake && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No reproductions yet" />
        )}
      </div>
    </aside>
  );
}
