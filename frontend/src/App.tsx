import { useMemo, useState } from "react";
import { CloudServerOutlined, ExperimentOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Avatar, Input, Tooltip } from "antd";
import { matchPath, useLocation, useNavigate } from "react-router-dom";
import { ApiError, api, getPrincipal, setPrincipal } from "./api/client";
import type { Intake, JobDetail, ReproductionSession } from "./api/types";
import { ConversationWorkspace } from "./components/ConversationWorkspace";
import { Inspector } from "./components/Inspector";
import { TaskHistory } from "./components/TaskHistory";
import { useReproductionEvents } from "./hooks/useReproductionEvents";
import { humanize } from "./utils/presentation";

function routeSelection(pathname: string): { intakeId?: string; jobId?: string; sessionId?: string } {
  const session = matchPath("/sessions/:sessionId", pathname);
  const intake = matchPath("/intakes/:intakeId", pathname);
  const job = matchPath("/reproductions/:jobId", pathname);
  return {
    sessionId: session?.params.sessionId,
    intakeId: intake?.params.intakeId,
    jobId: job?.params.jobId,
  };
}

function errorMessage(error: unknown): string | undefined {
  if (!error) return undefined;
  if (error instanceof ApiError && error.status === 403) {
    return "此复现任务属于其他用户。请切换回任务所属用户，或打开您自己的会话。";
  }
  if (error instanceof Error) return error.message;
  return "发生了意外的 API 错误。";
}

function activeJob(jobFromRoute: JobDetail | undefined, intakeJob: JobDetail | undefined): JobDetail | undefined {
  return jobFromRoute ?? intakeJob;
}

export default function App() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const selection = useMemo(() => routeSelection(location.pathname), [location.pathname]);
  const [principalDraft, setPrincipalDraft] = useState(getPrincipal());
  const [actionError, setActionError] = useState<string>();

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: api.listJobs,
    refetchInterval: 10_000,
  });
  const intakeQuery = useQuery({
    queryKey: ["intake", selection.intakeId],
    queryFn: () => api.getIntake(selection.intakeId!),
    enabled: Boolean(selection.intakeId),
    refetchInterval: (query) => query.state.data?.state === "analyzing" ? 2_500 : false,
  });
  const routeJobQuery = useQuery({
    queryKey: ["job", selection.jobId],
    queryFn: () => api.getJob(selection.jobId!),
    enabled: Boolean(selection.jobId),
    refetchInterval: (query) => ["queued", "claimed", "running", "cancel_requested"].includes(query.state.data?.state ?? "") ? 4_000 : false,
  });
  const derivedSessionId = selection.sessionId ?? routeJobQuery.data?.session_id ?? intakeQuery.data?.session_id ?? undefined;
  const sessionQuery = useQuery({
    queryKey: ["session", derivedSessionId],
    queryFn: () => api.getSession(derivedSessionId!),
    enabled: Boolean(derivedSessionId),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      if (["awaiting_clarification", "waiting_for_resource"].includes(data.status)) return 2_500;
      if (data.jobs.some((item) => ["queued", "claimed", "running", "cancel_requested"].includes(item.state))) return 4_000;
      return 8_000;
    },
  });
  const intakeJobId = intakeQuery.data?.job_id ?? undefined;
  const intakeJobQuery = useQuery({
    queryKey: ["job", intakeJobId],
    queryFn: () => api.getJob(intakeJobId!),
    enabled: Boolean(intakeJobId && !selection.jobId),
    refetchInterval: (query) => ["queued", "claimed", "running", "cancel_requested"].includes(query.state.data?.state ?? "") ? 4_000 : false,
  });
  const sessionJobs = sessionQuery.data?.jobs ?? [];
  const activeSessionJob = [...sessionJobs].reverse().find((item) => ["queued", "claimed", "running", "cancel_requested"].includes(item.state)) ?? sessionJobs.at(-1);
  const job = activeJob(routeJobQuery.data, intakeJobQuery.data) ?? activeSessionJob;
  const streamJobId = job?.job_id;
  const stream = useReproductionEvents(streamJobId);
  const hasCanonicalResult = job?.state === "succeeded";
  const resultsQuery = useQuery({
    queryKey: ["results", streamJobId],
    queryFn: () => api.getResults(streamJobId!),
    enabled: Boolean(streamJobId && hasCanonicalResult),
    retry: false,
  });
  const comparisonQuery = useQuery({
    queryKey: ["comparison", streamJobId],
    queryFn: () => api.getComparison(streamJobId!),
    enabled: Boolean(streamJobId && hasCanonicalResult),
    retry: false,
  });

  const refreshIntake = (intake: Intake) => {
    queryClient.setQueryData(["intake", intake.intake_id], intake);
    void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    if (intake.session_id) void queryClient.invalidateQueries({ queryKey: ["session", intake.session_id] });
  };
  const refreshSession = (session: ReproductionSession) => {
    queryClient.setQueryData(["session", session.session_id], session);
    void queryClient.invalidateQueries({ queryKey: ["jobs"] });
  };
  const applyClarifyOrResourceResult = (result: Intake | ReproductionSession) => {
    if ("origin_intake_id" in result) refreshSession(result);
    else refreshIntake(result);
  };
  const createMutation = useMutation({
    mutationFn: api.createIntake,
    onMutate: () => setActionError(undefined),
    onSuccess: (intake) => {
      refreshIntake(intake);
      if (intake.session_id) navigate(`/sessions/${encodeURIComponent(intake.session_id)}`);
      else navigate(`/intakes/${encodeURIComponent(intake.intake_id)}`);
    },
    onError: (error) => setActionError(errorMessage(error)),
  });
  const sessionId = selection.sessionId ?? sessionQuery.data?.session_id ?? intakeQuery.data?.session_id ?? undefined;
  const clarifyMutation = useMutation<Intake | ReproductionSession, Error, string[]>({
    mutationFn: (answers) => sessionId
      ? api.clarifySession(sessionId, answers)
      : api.clarify(selection.intakeId!, answers),
    onMutate: () => setActionError(undefined),
    onSuccess: applyClarifyOrResourceResult,
    onError: (error) => setActionError(errorMessage(error)),
  });
  const resourceMutation = useMutation<
    Intake | ReproductionSession,
    Error,
    { requirementId: string; hostPath: string }
  >({
    mutationFn: ({ requirementId, hostPath }) =>
      sessionId
        ? api.submitSessionResource(sessionId, requirementId, hostPath)
        : api.submitResource(selection.intakeId!, requirementId, hostPath),
    onMutate: () => setActionError(undefined),
    onSuccess: applyClarifyOrResourceResult,
    onError: (error) => setActionError(errorMessage(error)),
  });
  const startMutation = useMutation({
    mutationFn: () => sessionId ? api.startSession(sessionId) : api.start(selection.intakeId!),
    onMutate: () => setActionError(undefined),
    onSuccess: (startedJob) => {
      queryClient.setQueryData(["job", startedJob.job_id], startedJob);
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      if (startedJob.session_id) {
        void queryClient.invalidateQueries({ queryKey: ["session", startedJob.session_id] });
        navigate(`/sessions/${encodeURIComponent(startedJob.session_id)}`);
      } else {
        navigate(`/reproductions/${encodeURIComponent(startedJob.job_id)}`);
      }
    },
    onError: (error) => setActionError(errorMessage(error)),
  });
  const appendMutation = useMutation({
    mutationFn: (input: { goal?: string; experiment_ids?: string[] }) => api.appendExperiments(sessionId!, input),
    onMutate: () => setActionError(undefined),
    onSuccess: refreshSession,
    onError: (error) => setActionError(errorMessage(error)),
  });
  const cancelMutation = useMutation({
    mutationFn: () => api.cancel(job!.job_id),
    onMutate: () => setActionError(undefined),
    onSuccess: (cancelledJob) => {
      queryClient.setQueryData(["job", cancelledJob.job_id], cancelledJob);
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      if (cancelledJob.session_id) void queryClient.invalidateQueries({ queryKey: ["session", cancelledJob.session_id] });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const routeError = intakeQuery.error ?? routeJobQuery.error ?? intakeJobQuery.error ?? sessionQuery.error;
  const currentError = actionError ?? errorMessage(routeError);
  const actionLoading = clarifyMutation.isPending || resourceMutation.isPending || startMutation.isPending || cancelMutation.isPending || appendMutation.isPending;
  const gpuSummary = job?.waiting_reason
    ? "等待分配"
    : job?.gpu_allocation
      ? "已分配"
      : "后端管理";

  const applyPrincipal = () => {
    const normalized = principalDraft.trim();
    if (!normalized || normalized === getPrincipal()) return;
    setPrincipal(normalized);
    queryClient.clear();
    setActionError(undefined);
    navigate("/");
  };

  return (
    <div className="app-shell">
      <header className="global-header">
        <button className="brand" onClick={() => navigate("/")} aria-label="ReproPilot 首页">
          <span className="brand-mark"><ExperimentOutlined /></span>
          <span><strong>ReproPilot</strong><small>科研复现工作台</small></span>
        </button>
        <div className="global-signals">
          <Tooltip title={jobsQuery.isError ? "API 服务不可用" : "API 服务连接正常"}>
            <span className={`signal ${jobsQuery.isError ? "danger" : "healthy"}`}><CloudServerOutlined /> 服务{jobsQuery.isError ? "离线" : "在线"}</span>
          </Tooltip>
          <span className="signal"><span className="gpu-glyph">GPU</span> {gpuSummary}</span>
          <div className="principal-control">
            <Avatar size="small" icon={<SafetyCertificateOutlined />} />
            <Input
              value={principalDraft}
              onChange={(event) => setPrincipalDraft(event.target.value)}
              onBlur={applyPrincipal}
              onPressEnter={applyPrincipal}
              aria-label="当前用户"
              variant="borderless"
            />
          </div>
        </div>
      </header>
      {jobsQuery.isError && <Alert className="server-alert" banner type="warning" message="API 服务暂不可用。浏览器中的现有状态已保留，但此页面目前无法控制后台任务。" />}
      <div className="workspace-grid">
        <TaskHistory
          jobs={jobsQuery.data ?? []}
          sessions={sessionQuery.data ? [sessionQuery.data] : []}
          activeJobId={job?.job_id}
          activeIntake={intakeQuery.data}
          activeSessionId={sessionQuery.data?.session_id}
          loading={jobsQuery.isLoading}
          onNew={() => { setActionError(undefined); navigate("/"); }}
          onSelectJob={(jobId) => { setActionError(undefined); navigate(`/reproductions/${encodeURIComponent(jobId)}`); }}
          onSelectIntake={(intakeId) => { setActionError(undefined); navigate(`/intakes/${encodeURIComponent(intakeId)}`); }}
          onSelectSession={(id) => { setActionError(undefined); navigate(`/sessions/${encodeURIComponent(id)}`); }}
        />
        <ConversationWorkspace
          intake={intakeQuery.data}
          session={sessionQuery.data}
          job={job}
          events={stream.events}
          loading={intakeQuery.isLoading || routeJobQuery.isLoading || intakeJobQuery.isLoading || sessionQuery.isLoading}
          creating={createMutation.isPending}
          actionLoading={actionLoading}
          error={currentError}
          streamStatus={stream.status}
          onCreate={(input) => createMutation.mutate(input)}
          onClarify={(answers) => clarifyMutation.mutate(answers)}
          onResource={(requirementId, hostPath) => resourceMutation.mutate({ requirementId, hostPath })}
          onStart={() => startMutation.mutate()}
          onCancel={() => cancelMutation.mutate()}
          onAppendGoal={(goal) => appendMutation.mutate({ goal })}
          onRunExperiment={(experimentId) => appendMutation.mutate({ experiment_ids: [experimentId] })}
        />
        <Inspector
          intake={intakeQuery.data}
          session={sessionQuery.data}
          job={job}
          events={stream.events}
          results={resultsQuery.data}
          comparison={comparisonQuery.data}
          resultsLoading={resultsQuery.isLoading || comparisonQuery.isLoading}
        />
      </div>
      <footer className="mobile-context">三栏科研工作台 · 加宽窗口可查看完整任务详情。</footer>
      <span className="sr-only">当前队列状态：{humanize(job?.state)}</span>
    </div>
  );
}
