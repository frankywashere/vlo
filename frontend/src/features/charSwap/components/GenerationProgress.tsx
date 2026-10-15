import { useEffect, useState } from "react";
import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import type { LivePipelineJob } from "../services/livePipeline";
import { PANEL } from "./stepStyles";

export function GenerationProgress({ job, provider }: {
  job: LivePipelineJob;
  provider: "reAPI" | "ToAPIs";
}) {
  const [now, setNow] = useState(() => Date.now());
  const active = job.status === "generating" || job.status === "finalizing";
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, job.id]);
  if (!active) return null;
  const elapsed = job.generation_started_at
    ? Math.max(0, Math.floor(now / 1000 - job.generation_started_at)) : null;
  const checkedAgo = job.provider_checked_at
    ? Math.max(0, Math.floor(now / 1000 - job.provider_checked_at)) : null;
  const queued = job.provider_state === "queued" || job.provider_state === "pending";
  const processing = ["processing", "running", "generating"].includes(job.provider_state ?? "");
  const title = job.status === "finalizing"
    ? "Restoring your original soundtrack"
    : queued ? `Queued with ${provider}`
    : processing ? "MiniMax is generating your replacement"
    : "Waiting for a generation status update";
  return (
    <Box sx={PANEL}>
      <Stack spacing={1.5}>
        <Typography role="status" aria-live="polite" fontWeight={600}>{title}</Typography>
        <LinearProgress aria-label="Generation in progress" />
        {elapsed !== null && <Typography variant="body2">
          Elapsed: {Math.floor(elapsed / 60)}m {elapsed % 60}s
        </Typography>}
        <Typography variant="body2" color="text.secondary">
          {job.status === "finalizing"
            ? "Generation finished. Preparing your download at its generated resolution."
            : checkedAgo === null
              ? "Checking the provider automatically. No reliable percentage or finish time is available."
              : `Last successful provider check: ${checkedAgo}s ago. ${checkedAgo >= 30
                ? "Waiting for a fresh status update."
                : "Checking automatically; no reliable percentage or finish time is available."}`}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Keep this tab open. You do not need to submit again.
        </Typography>
      </Stack>
    </Box>
  );
}
