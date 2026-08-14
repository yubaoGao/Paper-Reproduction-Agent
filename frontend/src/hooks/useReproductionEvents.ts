import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ReproductionEventStream, type StreamStatus } from "../api/events";
import { getPrincipal } from "../api/client";
import type { ProductEvent } from "../api/types";

const MAX_CACHED_EVENTS = 500;

function eventCacheKey(jobId: string): string {
  return `repropilot.events.${getPrincipal()}.${jobId}`;
}

function cachedEvents(jobId: string): ProductEvent[] {
  try {
    const value = JSON.parse(localStorage.getItem(eventCacheKey(jobId)) || "[]") as ProductEvent[];
    if (!Array.isArray(value)) return [];
    return value
      .filter((item) => Number.isFinite(item.sequence) && typeof item.type === "string")
      .sort((left, right) => left.sequence - right.sequence)
      .slice(-MAX_CACHED_EVENTS);
  } catch {
    return [];
  }
}

export function useReproductionEvents(jobId?: string | null) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>("closed");
  const eventKey = jobId ?? "none";
  const { data: events = [] } = useQuery<ProductEvent[]>({
    queryKey: ["events", eventKey],
    queryFn: async () => [],
    initialData: () => (jobId ? cachedEvents(jobId) : []),
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
          const updated = [...existing, event]
            .sort((left, right) => left.sequence - right.sequence)
            .slice(-MAX_CACHED_EVENTS);
          localStorage.setItem(eventCacheKey(jobId), JSON.stringify(updated));
          return updated;
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
          void queryClient.invalidateQueries({ queryKey: ["session"] });
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
