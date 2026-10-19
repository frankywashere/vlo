import { Box, FormControlLabel, Stack, Switch, Typography } from "@mui/material";
import { useShallow } from "zustand/react/shallow";
import { useCharSwapStore } from "../useCharSwapStore";

export function GenerationOptions({ disabled = false, onChange }: { disabled?: boolean; onChange?: () => void }) {
  const defaults = useCharSwapStore(useShallow(state => ({
    manual: !(state.easyMode ?? true), enhanced: state.enhancedCharacterMasking ?? true,
    setEasyMode: state.setEasyMode, setEnhanced: state.setEnhancedCharacterMasking,
  })));
  const { manual, enhanced } = defaults;
  return (
    <Stack component="section" aria-label="Generation options" spacing={2.5}
      sx={{ p: 2.5, border: "1px solid", borderColor: "divider", borderRadius: "5px", bgcolor: "background.paper" }}>
      <Typography component="h2" variant="subtitle1" fontWeight={700}>Generation options</Typography>
      <Box>
        <FormControlLabel sx={{ m: 0, width: "100%", justifyContent: "space-between" }} labelPlacement="start"
          control={<Switch checked={manual} disabled={disabled} onChange={(_, value) => { onChange?.(); defaults.setEasyMode(!value); }}
            slotProps={{ input: { role: "switch", "aria-describedby": "manual-review-help" } }} />}
          label={<Typography variant="body2" fontWeight={650}>Review scene changes manually</Typography>} />
        <Typography id="manual-review-help" variant="body2" color="text.secondary" sx={{ mt: .5 }}>
          Use for cuts or moments where tracking may follow the wrong person.
        </Typography>
      </Box>
      <Box>
        <FormControlLabel sx={{ m: 0, width: "100%", justifyContent: "space-between" }} labelPlacement="start"
          control={<Switch checked={enhanced} disabled={disabled} onChange={(_, value) => { onChange?.(); defaults.setEnhanced(value); }}
            slotProps={{ input: { role: "switch", "aria-describedby": "enhanced-mask-help" } }} />}
          label={<Typography variant="body2" fontWeight={650}>Enhanced person masking</Typography>} />
        <Typography id="enhanced-mask-help" variant="body2" color="text.secondary" sx={{ mt: .5 }}>
          Adds a visual guide when the person is hard to distinguish in the scene.
        </Typography>
      </Box>
      {manual && <Typography variant="caption" color="text.secondary">You’ll check scene matches before generation.</Typography>}
    </Stack>
  );
}
