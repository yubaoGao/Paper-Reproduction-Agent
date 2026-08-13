import { useMemo, useState } from "react";
import { CloudServerOutlined, ExperimentOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Avatar, Input, Tooltip } from "antd";
import { matchPath, useLocation, useNavigate } from "react-router-dom";
import { ApiError, api, getPrincipal, setPrincipal } from "./api/client";
import type { Intake, JobDetail } from "./api/types";
import { ConversationWorkspace } from "./components/ConversationWorkspace";
import { Inspector } from "./components/Inspector";
import { TaskHistory } from "./components/TaskHistory";
import { useReproductionEvents } from "./hooks/useReproductionEvents";
import { humanize } from "./utils/presentation";

function routeSelection(pathname: string): { intakeId?: string; jobId?: string } {
  const intake = matchPath("/intakes/:intakeId", pathname);
  const job = matchPath("/reproductions/:jobId", pathname);
  return { intakeId: intake?.params.intakeId, jobId: job?.params.jobId };
}

function errorMessage(error: unknown): string | undefined {
  if (!error) return undefined;
  if (error instanceof ApiError && error.status === 403) {
    return "This reproduction belongs to another principal. Switch back to its owner or open one of your own sessions.";
  }
  if (error instanceof Error) return error.message;
  return "An unexpected API error occurred.";
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
  const intakeJobId = intakeQuery.data?.job_id ?? undefined;
  const intakeJobQuery = useQuery({
    queryKey: ["job", intakeJobId],
    queryFn: () => api.getJob(intakeJobId!),
    enabled: Boolean(intakeJobId && !selection.jobId),
    refetchInterval: (query) => ["queued", "claimed", "running", "cancel_requested"].includes(query.state.data?.state ?? "") ? 4_000 : false,
  });
  const job = activeJob(routeJobQuery.data, intakeJobQuery.data);
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
  };
  const createMutation = useMutation({
    mutationFn: api.createIntake,
    onMutate: () => setActionError(undefined),
    onSuccess: (intake) => {
      refreshIntake(intake);
      navigate(`/intakes/${encodeURIComponent(intake.intake_id)}`);
    },
    onError: (error) => setActionError(errorMessage(error)),
  });
  const clarifyMutation = useMutation({
    mutationFn: (answers: string[]) => api.clarify(selection.intakeId!, answers),
    onMutate: () => setActionError(undefined),
    onSuccess: refreshIntake,
    onError: (error) => setActionError(errorMessage(error)),
  });
  const resourceMutation = useMutation({
    mutationFn: ({ requirementId, hostPath }: { requirementId: string; hostPath: string }) =>
      api.submitResource(selection.intakeId!, requirementId, hostPath),
    onMutate: () => setActionError(undefined),
    onSuccess: refreshIntake,
    onError: (error) => setActionError(errorMessage(error)),
  });
  const startMutation = useMutation({
    mutationFn: () => api.start(selection.intakeId!),
    onMutate: () => setActionError(undefined),
    onSuccess: (startedJob) => {
      queryClient.setQueryData(["job", startedJob.job_id], startedJob);
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      navigate(`/reproductions/${encodeURIComponent(startedJob.job_id)}`);
    },
    onError: (error) => setActionError(errorMessage(error)),
  });
  const cancelMutation = useMutation({
    mutationFn: () => api.cancel(job!.job_id),
    onMutate: () => setActionError(undefined),
    onSuccess: (cancelledJob) => {
      queryClient.setQueryData(["job", cancelledJob.job_id], cancelledJob);
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const routeError = intakeQuery.error ?? routeJobQuery.error ?? intakeJobQuery.error;
  const currentError = actionError ?? errorMessage(routeError);
  const actionLoading = clarifyMutation.isPending || resourceMutation.isPending || startMutation.isPending || cancelMutation.isPending;
  const gpuSummary = job?.waiting_reason
    ? "Waiting"
    : job?.gpu_allocation
      ? "Allocated"
      : "Backend managed";

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
        <button className="brand" onClick={() => navigate("/")} aria-label="ReproPilot home">
          <span className="brand-mark"><ExperimentOutlined /></span>
          <span><strong>ReproPilot</strong><small>Scientific reproduction workspace</small></span>
        </button>
        <div className="global-signals">
          <Tooltip title={jobsQuery.isError ? "API unavailable" : "Task 16 API reachable"}>
            <span className={`signal ${jobsQuery.isError ? "danger" : "healthy"}`}><CloudServerOutlined /> Server {jobsQuery.isError ? "offline" : "online"}</span>
          </Tooltip>
          <span className="signal"><span className="gpu-glyph">GPU</span> {gpuSummary}</span>
          <div className="principal-control">
            <Avatar size="small" icon={<SafetyCertificateOutlined />} />
            <Input
              value={principalDraft}
              onChange={(event) => setPrincipalDraft(event.target.value)}
              onBlur={applyPrincipal}
              onPressEnter={applyPrincipal}
              aria-label="Current principal"
              variant="borderless"
            />
          </div>
        </div>
      </header>
      {jobsQuery.isError && <Alert className="server-alert" banner type="warning" message="The Task 16 API is unavailable. Existing browser state is preserved; background jobs are not controlled by this page." />}
      <div className="workspace-grid">
        <TaskHistory
          jobs={jobsQuery.data ?? []}
          activeJobId={job?.job_id}
          activeIntake={intakeQuery.data}
          loading={jobsQuery.isLoading}
          onNew={() => { setActionError(undefined); navigate("/"); }}
          onSelectJob={(jobId) => { setActionError(undefined); navigate(`/reproductions/${encodeURIComponent(jobId)}`); }}
          onSelectIntake={(intakeId) => { setActionError(undefined); navigate(`/intakes/${encodeURIComponent(intakeId)}`); }}
        />
        <ConversationWorkspace
          intake={intakeQuery.data}
          job={job}
          events={stream.events}
          loading={intakeQuery.isLoading || routeJobQuery.isLoading || intakeJobQuery.isLoading}
          creating={createMutation.isPending}
          actionLoading={actionLoading}
          error={currentError}
          streamStatus={stream.status}
          onCreate={(input) => createMutation.mutate(input)}
          onClarify={(answers) => clarifyMutation.mutate(answers)}
          onResource={(requirementId, hostPath) => resourceMutation.mutate({ requirementId, hostPath })}
          onStart={() => startMutation.mutate()}
          onCancel={() => cancelMutation.mutate()}
        />
        <Inspector
          intake={intakeQuery.data}
          job={job}
          events={stream.events}
          results={resultsQuery.data}
          comparison={comparisonQuery.data}
          resultsLoading={resultsQuery.isLoading || comparisonQuery.isLoading}
        />
      </div>
      <footer className="mobile-context">Three-panel research workspace · widen the window for the full inspector.</footer>
      <span className="sr-only">Current queue state: {humanize(job?.state)}</span>
    </div>
  );
}
