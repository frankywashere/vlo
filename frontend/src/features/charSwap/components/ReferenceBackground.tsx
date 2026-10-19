import {Alert, Stack, ToggleButton, ToggleButtonGroup, Typography} from "@mui/material";
import type {ReferenceAsset} from "../model/model";
import {CleanupProgressIndicator} from "./CleanupProgressIndicator";
import {useReferenceCleanupDraft} from "./ReferenceCleanupProvider";

interface ReferenceBackgroundProps {targetId:string;reference:ReferenceAsset;cleanupEndpoint?:string}
export function ReferenceBackground({targetId,reference}:ReferenceBackgroundProps) {
  const draft=useReferenceCleanupDraft();
  const progress=draft.progress[targetId] ?? {phase:"queued"};
  return <Stack spacing={1}>
    <ToggleButtonGroup exclusive size="small" value={reference.cleanup?.mode ?? "original"}
      aria-label="Reference background">
      <ToggleButton value="original" onClick={()=>draft.choose(targetId,"original")}>Original</ToggleButton>
      <ToggleButton value="clean" onClick={()=>draft.choose(targetId,"clean")} disabled={reference.cleanup?.unsupported || reference.cleanup?.interrupted}>Clean background</ToggleButton>
    </ToggleButtonGroup>
    {reference.cleanup?.mode==="clean" && (reference.cleanup.pending || reference.cleanup.cleanedUrl) && <Stack spacing={.5}>
      <CleanupProgressIndicator progress={progress} complete={!reference.cleanup.pending&&Boolean(reference.cleanup.cleanedUrl)}/>
      {reference.cleanup.pending && <Typography variant="caption" color="text.secondary">{progress.phase==="loading"
        ?`First use downloads ${progress.downloadMB ?? 99} MB and may take a few minutes. `:""}You can use Original while this finishes.</Typography>}
    </Stack>}
    {reference.cleanup?.error && <Alert severity="info">{reference.cleanup.error}</Alert>}
  </Stack>;
}
