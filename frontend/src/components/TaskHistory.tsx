import { ClockCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Empty, Skeleton, Tooltip } from "antd";
import type { Intake, JobDetail, ReproductionSession } from "../api/types";
import { formatTime } from "../utils/presentation";
import { StatusPill } from "./StatusPill";

interface Props {
  jobs: JobDetail[];
  sessions?: ReproductionSession[];
  intakes?: Intake[];
  activeJobId?: string;
  activeIntake?: Intake;
  activeSessionId?: string;
  loading: boolean;
  onNew: () => void;
  onSelectJob: (jobId: string) => void;
  onSelectIntake: (intakeId: string) => void;
  onSelectSession?: (sessionId: string) => void;
}

export function TaskHistory({
  jobs, sessions = [], intakes = [], activeJobId, activeIntake, activeSessionId, loading,
  onNew, onSelectJob, onSelectIntake, onSelectSession,
}: Props) {
  const visibleIntakes = intakes.filter((intake) =>
    ["analyzing", "failed", "ambiguous", "waiting_for_resource"].includes(intake.state)
    && !sessions.some((session) => session.origin_intake_id === intake.intake_id)
    && !jobs.some((job) => job.job_id === intake.job_id)
  );
  return (
    <aside className="history-panel" aria-label="复现会话">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">工作台</span>
          <h2>复现任务</h2>
        </div>
        <span className="history-count">{jobs.length}</span>
      </div>
      <Button type="primary" icon={<PlusOutlined />} block size="large" onClick={onNew}>
        新建复现
      </Button>
      <div className="history-section-label">最近会话</div>
      <div className="history-list">
        {loading && <Skeleton active paragraph={{ rows: 5 }} title={false} />}
        {!loading && visibleIntakes.map((intake) => (
          <button
            className={`history-item ${activeIntake?.intake_id === intake.intake_id ? "active" : ""}`}
            key={intake.intake_id}
            onClick={() => onSelectIntake(intake.intake_id)}
          >
            <div className="history-item-title">{intake.goal}</div>
            <div className="history-item-meta">
              <StatusPill status={intake.state} />
              <span><ClockCircleOutlined /> {formatTime(intake.updated_at)}</span>
            </div>
          </button>
        ))}
        {!loading && sessions.map((session) => (
          <button
            className={`history-item ${activeSessionId === session.session_id ? "active" : ""}`}
            key={session.session_id}
            onClick={() => onSelectSession ? onSelectSession(session.session_id) : onSelectIntake(session.origin_intake_id)}
          >
            <Tooltip title={session.repository_url} placement="right">
              <div className="history-item-title">{session.goal || session.source_filename}</div>
            </Tooltip>
            <div className="history-item-meta">
              <StatusPill status={session.status} />
              <span><ClockCircleOutlined /> {formatTime(session.updated_at)}</span>
            </div>
          </button>
        ))}
        {!loading && activeIntake && !jobs.some((job) => job.job_id === activeIntake.job_id) && !sessions.some((session) => session.origin_intake_id === activeIntake.intake_id) && !visibleIntakes.some((item) => item.intake_id === activeIntake.intake_id) && (
          <button className="history-item active" onClick={() => onSelectIntake(activeIntake.intake_id)}>
            <div className="history-item-title">{activeIntake.goal}</div>
            <div className="history-item-meta"><StatusPill status={activeIntake.state} /><span><ClockCircleOutlined /> {formatTime(activeIntake.created_at)}</span></div>
          </button>
        )}
        {!loading && jobs.map((job) => (
          <button
            className={`history-item ${activeJobId === job.job_id ? "active" : ""}`}
            key={job.job_id}
            onClick={() => job.session_id && onSelectSession ? onSelectSession(job.session_id) : onSelectJob(job.job_id)}
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
        {!loading && jobs.length === 0 && visibleIntakes.length === 0 && sessions.length === 0 && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无复现任务" />
        )}
      </div>
    </aside>
  );
}
