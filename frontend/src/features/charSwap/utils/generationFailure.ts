import type { LivePipelineJob } from "../services/livePipeline";

type FailureJob = Pick<LivePipelineJob, "failure_category" | "message"> & { status: string };

export function generationFailureLabel(job: FailureJob): string | null {
  if (job.status !== "failed") return null;
  if (job.failure_category === "content_rejected") return "Content safety";
  if (job.failure_category === "face_rejected") return "Face rejected";
  return "Failed";
}

export function generationFailureMessage(job: FailureJob): string {
  if (job.message) return job.message;
  if (job.failure_category === "content_rejected")
    return "The generation provider rejected this request during content-safety review. It did not identify which input triggered the rejection.";
  if (job.failure_category === "face_rejected")
    return "The generation provider rejected a face in the submitted media.";
  return "This generation failed. Review its setup before submitting a new job.";
}
