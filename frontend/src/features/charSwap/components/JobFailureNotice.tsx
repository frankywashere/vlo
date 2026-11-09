import { Alert, Chip, Stack, Typography } from "@mui/material";
import type { LivePipelineJob } from "../services/livePipeline";
import { generationFailureLabel, generationFailureMessage } from "../utils/generationFailure";
import { formatElapsed } from "./JobElapsedTime";

interface JobFailureNoticeProps {
  job: Pick<LivePipelineJob, "failure_category" | "message" | "failed_at" | "generation_started_at"> & {status: string};
}

export function JobFailureNotice({ job }: JobFailureNoticeProps) {
  const label = generationFailureLabel(job);
  if (!label) return null;
  const generationSeconds = job.failed_at !== undefined && job.generation_started_at !== undefined
    ? job.failed_at - job.generation_started_at : undefined;
  return <Stack spacing={1}>
    <Chip size="small" color="error" label={label} sx={{alignSelf: "flex-start"}} />
    <Alert severity="error">{generationFailureMessage(job)}</Alert>
    {generationSeconds !== undefined && generationSeconds >= 0 && <Typography variant="body2" color="text.secondary">
      Provider processing stopped after {formatElapsed(generationSeconds)}. Preparation and review time are separate.
    </Typography>}
  </Stack>;
}
