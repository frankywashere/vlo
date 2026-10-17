import type { AccountJob } from "../services/accounts";

export function isGenerationRunning(job: AccountJob) {
  return [
    "queued",
    "preparing",
    "generating",
    "finalizing",
    "submission_unknown",
  ].includes(job.status);
}
