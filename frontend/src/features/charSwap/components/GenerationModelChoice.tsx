import { Alert, Button, FormControl, FormControlLabel, FormLabel, MenuItem, Select, Radio, RadioGroup, Typography } from "@mui/material";
import { useShallow } from "zustand/react/shallow";
import { useCharSwapStore } from "../useCharSwapStore";
import type { GenerationModel } from "../model/model";

export function GenerationModelChoice({disabled = false, klingProviderAvailable = true}: {disabled?: boolean; klingProviderAvailable?: boolean}) {
  const {enabled, model, trim, select, resolution, setResolution, qualityEnabled} = useCharSwapStore(useShallow(state => ({
    qualityEnabled:state.minimax2kEnabled!==false,
    resolution:state.generationResolution ?? "768P",setResolution:state.setGenerationResolution,
    enabled: state.kling4Enabled, model: state.generationModel ?? "minimax-h3", trim: state.trim, select: state.setGenerationModel,
  })));
  const quality=<FormControl fullWidth size="small" disabled={disabled}><FormLabel>MiniMax quality</FormLabel><Select inputProps={{"aria-label":"MiniMax quality"}} value={resolution === "2K" ? "2K" : "768P"} onChange={event=>setResolution(event.target.value as "768P"|"2K")}><MenuItem value="768P">768p · Standard</MenuItem>{(qualityEnabled||resolution==="2K")&&<MenuItem value="2K" disabled={!qualityEnabled}>2K · {qualityEnabled?"Higher detail":"Currently unavailable"}</MenuItem>}</Select><Typography variant="caption" color="text.secondary">{qualityEnabled?"2K costs more. The guide keeps more detail when your original footage supports it.":"2K is currently unavailable. Use 768p to continue."}</Typography></FormControl>;
  if (!enabled) return model === "kling-4-0" ? <Alert severity="warning">Kling 4.0 is currently unavailable. Shorten your clip to 15 seconds and choose MiniMax to continue.<Button disabled={disabled || trim[1] - trim[0] > 15} onClick={() => select("minimax-h3")}>Use MiniMax</Button></Alert> : quality;
  const selectionTooLong = Number((trim[1] - trim[0]).toFixed(6)) > 15;
  return <FormControl component="fieldset" disabled={disabled}>
    <FormLabel component="legend">Generation model</FormLabel>
    <RadioGroup value={model} onChange={(_, value) => select(value as GenerationModel)}>
      <FormControlLabel value="minimax-h3" disabled={disabled || selectionTooLong} control={<Radio />} label="MiniMax H3 · up to 15 seconds" />
      <FormControlLabel value="kling-4-0" disabled={disabled || selectionTooLong || !klingProviderAvailable} control={<Radio />} label="Kling 4.0 · up to 15 seconds" />
    </RadioGroup>
    <Typography variant="caption" color="text.secondary">
      {selectionTooLong ? "Shorten your clip to 15 seconds before submitting." : "Your credit estimate updates for the selected model."}
      {model === "kling-4-0" ? " Uses colored person masks with face mesh off." : ""}
    </Typography>
    {model === "minimax-h3" ? quality : <Typography variant="caption" color="text.secondary">Kling quality: 720p</Typography>}
    {!klingProviderAvailable && <Typography variant="caption" color="text.secondary">Kling requires reAPI. Select reAPI in your API key settings to use it.</Typography>}
  </FormControl>;
}
