import type { LivePipelineJob } from "../services/livePipeline";

export function processingStageCopy(job?: {processing_stage?: LivePipelineJob["processing_stage"]; status: string} | null) {
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
