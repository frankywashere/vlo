import type { AccountJob } from "../services/accounts";

export function libraryExport(jobs: AccountJob[]) {
  // Signed media links and session credentials do not belong in a lasting export.
  return JSON.stringify(
    {
      generations: jobs.map((job) => ({
        id: job.id,
        title: job.title,
        status: job.status,
        created_at: job.created_at,
        completed_at: job.completed_at,
        expires_at: job.expires_at,
        duration_seconds: job.duration,
        width: job.width,
        height: job.height,
      })),
    },
    null,
    2,
  );
}
