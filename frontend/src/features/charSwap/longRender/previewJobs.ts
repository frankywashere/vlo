import type { AccountJob } from "../services/accounts";
import { longRenderSegments } from "./api";
import type { LongRenderJob } from "./api";

/** Both backends use the same presentation contract; orchestration remains isolated. */
export function toPreviewAccountJob(job: LongRenderJob): AccountJob {
  const statuses: Record<string, AccountJob["status"]> = {
    draft: "ready", created: "ready", uploading: "ready", uploaded: "ready", awaiting_upload: "ready",
    queued: "queued", preparing: "preparing", matching: "preparing", review_queued: "preparing", reviewing: "preparing",
    needs_review: "tracking_review", needs_attention: "tracking_review", tracking_review: "tracking_review",
    ready: "ready", generating: "generating", finalizing: "finalizing", assembling: "finalizing", comparison_processing: "finalizing",
    completed: "completed", failed: "failed", cancelled: "failed", canceled: "failed", expired: "expired", submission_unknown: "submission_unknown",
  };
  return {
    id: job.id,
    title: job.title ?? "Your recast",
    status: job.status === "ready" && job.auto_generate ? "queued" : statuses[job.status] ?? "submission_unknown",
    ...(job.status === "ready" && job.auto_generate ? { processing_stage: "generation_queue" as const } : {}),
    created_at: job.created_at ?? 0,
    updated_at: job.updated_at,
    duration: job.duration,
    width: job.width,
    height: job.height,
    provider: "reAPI",
    access_mode: "credits",
    easy_mode: job.easy_mode,
    message: ["cancelled", "canceled"].includes(job.status) ? "This recast was cancelled." : job.message,
    thumbnail_url: job.thumbnail_url ?? job.poster_url,
    output_url: job.output_url,
    guide_url: job.guide_url,
    source_preview_url: job.status === "completed" ? job.before_url ?? job.source_preview_url : job.source_preview_url ?? job.before_url,
    reference_urls: job.reference_urls,
    selection_targets: job.selection_targets,
    settings_used: job.settings_used,
    tracking_url: job.tracking_url,
    tracking_segments: longRenderSegments(job),
    tracking_revision: job.tracking_revision,
    tracking_choices: job.tracking_choices,
    tracking_error_segment_id: job.tracking_error_segment_id,
    generation_credits: job.generation_credits,
    consumer_credits_used: job.consumer_credits_used ?? job.test_credits_charged,
    consumer_credits_returned: job.consumer_credits_returned,
    comparison_url: job.comparison_url,
    comparison_status: job.comparison_status === "ready" || job.comparison_status === "failed" ? job.comparison_status : undefined,
    comparison_layout: (job.height ?? 0) > (job.width ?? 0) ? "side_by_side" : "stacked",
  };
}
