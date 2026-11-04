import type { LivePipelineJob } from "../services/livePipeline";

export function processingStageCopy(job?: Pick<LivePipelineJob, "processing_stage" | "generation_destination" | "self_hosted_progress" | "generation_fallback"> & {status: string} | null) {
  if (job && ["queued", "preparing", "generating"].includes(job.status) && job.generation_destination === "google_h3") {
    if (job.self_hosted_progress?.phase === "starting_worker")
      return {title: "Starting Google GPU", detail: "Your clip is saved. We’re starting the Google worker and loading its models before rendering."};
    if (job.self_hosted_progress?.phase === "waiting_for_capacity")
      return {title: "Waiting for Google GPU", detail: "Your clip is queued while Google GPU capacity is checked. No replacement video has been rendered yet."};
  }
  if (job && ["queued", "preparing"].includes(job.status) && job.generation_destination === "current" &&
      job.generation_fallback?.from === "google_h3" && job.generation_fallback.reason === "capacity_unavailable" &&
      (!job.processing_stage || job.processing_stage === "generation_queue"))
    return {title: "Preparing with the normal API route", detail: "Google GPU capacity was unavailable. We’re restarting normal preparation, including SAM when enabled, with the normal route’s saved settings."};
  if (!job || ((job.processing_stage === "guide_rebuild" || job.processing_stage === "mask_recovery")
      && !["queued", "preparing"].includes(job.status))
      || (job.processing_stage === "generation_queue" && job.status !== "queued")
      || (job.processing_stage === "guide_review" && job.status !== "ready")) return undefined;
  switch (job.processing_stage) {
    case "guide_rebuild":
      return {title: "Building your guide", detail: "Your scene choices are saved. We’re building the masked preview using the masks and depth already prepared."};
    case "mask_recovery":
      return {title: "Recovering missing masks", detail: "We’re retracking only the missing selections. Your other choices are saved."};
    case "generation_queue":
      return {title: "Generation queued", detail: "Your guide is ready. Rendering will start automatically."};
    case "guide_review":
      return {title: "Review your guide", detail: "Check the masked preview, then approve it to start rendering."};
    default:
      return undefined;
  }
}
