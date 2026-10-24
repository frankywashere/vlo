import type { AccountJob } from "../services/accounts";
import { processingStageCopy } from "./processingStage";

const STATUS_LABELS: Record<AccountJob["status"], string> = {
  queued: "In queue",
  preparing: "Preparing",
  tracking_review: "Needs attention",
  ready: "Waiting for approval",
  generating: "Generating",
  finalizing: "Finishing",
  completed: "Complete",
  failed: "Failed",
  expired: "Expired",
  submission_unknown: "Checking submission",
};

export function generationStatus(job: AccountJob) {
  const color =
    job.status === "completed"
      ? "success"
      : job.status === "failed" || job.status === "expired"
        ? "error"
        : [
              "generating",
              "finalizing",
              "tracking_review",
              "ready",
              "submission_unknown",
            ].includes(job.status)
          ? "warning"
          : "info";
  return {
    label:
      job.status === "ready"
        ? STATUS_LABELS.ready
        : (processingStageCopy(job)?.title ?? STATUS_LABELS[job.status]),
    color,
  } as const;
}
