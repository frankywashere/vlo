import { useEffect, useState } from "react";
import { Typography } from "@mui/material";
import type { AccountJob } from "../services/accounts";

interface JobElapsedTimeProps {
  job: Pick<AccountJob, "status" | "created_at" | "completed_at" | "updated_at">;
}

export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  return hours > 0 ? `${hours}h ${minutes}m ${rest}s` : minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`;
}

export function JobElapsedTime({ job }: JobElapsedTimeProps) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const terminal = ["completed", "failed", "expired"].includes(job.status);
  useEffect(() => {
    setNow(Date.now() / 1000);
    if (terminal) return;
    const timer = globalThis.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => globalThis.clearInterval(timer);
  }, [job.created_at, terminal]);
  // Older completed jobs predate completed_at; their last persisted update was export.
  const end = terminal ? job.completed_at ?? (job.status !== "expired" ? job.updated_at : undefined) : now;
  if (!Number.isFinite(job.created_at) || job.created_at <= 0 || !Number.isFinite(end)) return null;
  const label = job.status === "completed" ? "Finished in" : terminal ? "Stopped after" : "Elapsed";
  return <Typography variant="body2" color="text.secondary" title="Total time since upload was registered, including queue and any time awaiting your review.">
    {label}: {formatElapsed(end! - job.created_at)}
  </Typography>;
}
