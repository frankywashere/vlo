import {Alert, CircularProgress, Stack, ToggleButton, ToggleButtonGroup, Typography} from "@mui/material";
import type {ReferenceAsset} from "../model/model";
import {useReferenceCleanupDraft} from "./ReferenceCleanupProvider";

interface ReferenceBackgroundProps {targetId:string;reference:ReferenceAsset;cleanupEndpoint?:string}
export function ReferenceBackground({targetId,reference}:ReferenceBackgroundProps) {
  const draft=useReferenceCleanupDraft();
  const progress=draft.progress[targetId] ?? {phase:"queued"};
  return <Stack spacing={1}>
    <ToggleButtonGroup exclusive size="small" value={reference.cleanup?.mode ?? "original"}
      aria-label="Reference background">
      <ToggleButton value="original" onClick={()=>draft.choose(targetId,"original")}>Original</ToggleButton>
      <ToggleButton value="clean" onClick={()=>draft.choose(targetId,"clean")} disabled={reference.cleanup?.unsupported}>Clean background</ToggleButton>
    </ToggleButtonGroup>
    {reference.cleanup?.pending && reference.cleanup.mode==="clean" && <Stack direction="row" spacing={1} alignItems="center" role="status">
      <CircularProgress size={14}/><Typography variant="caption">{progress.phase==="queued"?"Waiting for the other character…":progress.phase==="loading"
        ?`Loading cleanup${progress.percent===undefined?"":` ${progress.percent}%`}… First use downloads ${progress.downloadMB ?? 99} MB and may take a few minutes.`
        :"Cleaning background…"} You can use Original while this finishes.</Typography>
    </Stack>}
    {reference.cleanup?.error && <Alert severity="info">{reference.cleanup.error}</Alert>}
  </Stack>;
}
