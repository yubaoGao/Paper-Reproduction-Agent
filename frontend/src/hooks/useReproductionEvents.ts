import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ReproductionEventStream, type StreamStatus } from "../api/events";
import type { ProductEvent } from "../api/types";

export function useReproductionEvents(jobId?: string | null) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>("closed");
  const eventKey = jobId ?? "none";
  const { data: events = [] } = useQuery<ProductEvent[]>({
    queryKey: ["events", eventKey],
    queryFn: async () => [],
    initialData: [],
    enabled: false,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (!jobId) {
      setStatus("closed");
      return;
    }
    const stream = new ReproductionEventStream(
      jobId,
      (event) => {
        queryClient.setQueryData<ProductEvent[]>(["events", jobId], (existing = []) => {
          if (existing.some((item) => item.sequence === event.sequence)) return existing;
          return [...existing, event].sort((left, right) => left.sequence - right.sequence);
        });
        void queryClient.invalidateQueries({ queryKey: ["job", jobId] });
        if (["FINAL_RESULT_ACQUIRED", "JOB_SUCCEEDED"].includes(event.type)) {
          void queryClient.invalidateQueries({ queryKey: ["results", jobId] });
        }
        if (event.type === "COMPARISON_COMPLETED") {
          void queryClient.invalidateQueries({ queryKey: ["comparison", jobId] });
        }
        if (["JOB_SUCCEEDED", "JOB_FAILED", "JOB_CANCELLED", "JOB_QUEUED"].includes(event.type)) {
          void queryClient.invalidateQueries({ queryKey: ["jobs"] });
        }
      },
      setStatus,
    );
    stream.connect();
    return () => stream.close();
  }, [jobId, queryClient]);

  return {
    events,
    status,
  };
}
