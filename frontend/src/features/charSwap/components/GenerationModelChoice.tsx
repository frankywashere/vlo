import { Alert, Button, FormControl, FormControlLabel, FormLabel, Radio, RadioGroup, Typography } from "@mui/material";
import { useShallow } from "zustand/react/shallow";
import { useCharSwapStore } from "../useCharSwapStore";
import type { GenerationModel } from "../model/model";

export function GenerationModelChoice({disabled = false, klingProviderAvailable = true}: {disabled?: boolean; klingProviderAvailable?: boolean}) {
  const {enabled, model, trim, select} = useCharSwapStore(useShallow(state => ({
    enabled: state.kling4Enabled, model: state.generationModel ?? "minimax-h3", trim: state.trim, select: state.setGenerationModel,
  })));
  if (!enabled) return model === "kling-4-0" ? <Alert severity="warning">Kling 4.0 is currently unavailable. Shorten your clip to 15 seconds and choose MiniMax to continue.<Button disabled={disabled || trim[1] - trim[0] > 15} onClick={() => select("minimax-h3")}>Use MiniMax</Button></Alert> : null;
  const requiresKling = Number((trim[1] - trim[0]).toFixed(6)) > 15;
  return <FormControl component="fieldset" disabled={disabled}>
    <FormLabel component="legend">Generation model</FormLabel>
    <RadioGroup value={model} onChange={(_, value) => select(value as GenerationModel)}>
      <FormControlLabel value="minimax-h3" disabled={disabled || requiresKling} control={<Radio />} label="MiniMax H3 · up to 15 seconds" />
      <FormControlLabel value="kling-4-0" disabled={disabled || !klingProviderAvailable} control={<Radio />} label="Kling 4.0 · up to 30 seconds" />
    </RadioGroup>
    <Typography variant="caption" color="text.secondary">
      {requiresKling ? "Clips longer than 15 seconds require Kling 4.0." : "Your credit estimate updates for the selected model."}
      {model === "kling-4-0" ? " Uses colored person masks with face mesh off." : ""}
    </Typography>
    {!klingProviderAvailable && <Typography variant="caption" color="text.secondary">Kling requires reAPI. Select reAPI in your API key settings to use it.</Typography>}
  </FormControl>;
}
