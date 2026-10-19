import { Box, FormControlLabel, Stack, Switch, Typography } from "@mui/material";
import type { FaceMeshOptions } from "../services/livePipeline";
import { useShallow } from "zustand/react/shallow";
import { useCharSwapStore } from "../useCharSwapStore";

export function GenerationOptions({ disabled = false, onChange, watermarkOffer, faceMeshOptions }: { disabled?: boolean; onChange?: () => void; watermarkOffer?: {bonusCredits: number}; faceMeshOptions?: FaceMeshOptions }) {
  const defaults = useCharSwapStore(useShallow(state => ({
    faceMesh: state.faceMesh, setFaceMesh: state.setFaceMesh,
    easyMode: state.easyMode ?? true, enhanced: state.enhancedCharacterMasking ?? true,
    watermark: state.addWatermark ?? false, setWatermark: state.setAddWatermark,
    silent: state.submitWithoutAudio ?? false, setSilent: state.setSubmitWithoutAudio,
    setEasyMode: state.setEasyMode, setEnhanced: state.setEnhancedCharacterMasking,
  })));
  const { easyMode, enhanced } = defaults;
  return (
    <Stack component="section" aria-label="Generation options" spacing={2.5}
      sx={{ p: 2.5, border: "1px solid", borderColor: "divider", borderRadius: "5px", bgcolor: "background.paper" }}>
      <Typography component="h2" variant="subtitle1" fontWeight={700}>Generation options</Typography>
      {faceMeshOptions?.face_mesh_user_control && <Box>
        <FormControlLabel sx={{m: 0, width: "100%", justifyContent: "space-between"}} labelPlacement="start"
          control={<Switch checked={defaults.faceMesh ?? (enhanced ? faceMeshOptions.face_mesh_masked_default : faceMeshOptions.face_mesh_unmasked_default)}
            disabled={disabled} onChange={(_, value) => {onChange?.(); defaults.setFaceMesh(value);}}
            slotProps={{input: {role: "switch", "aria-describedby": "face-mesh-help"}}} />}
          label={<Typography variant="body2" fontWeight={650}>Face mesh</Typography>} />
        <Typography id="face-mesh-help" variant="body2" color="text.secondary" sx={{mt: .5}}>Adds facial-motion guidance for selected people. May take extra time.</Typography>
      </Box>}
      <Box>
        <FormControlLabel sx={{ m: 0, width: "100%", justifyContent: "space-between" }} labelPlacement="start"
          control={<Switch checked={easyMode} disabled={disabled} onChange={(_, value) => { onChange?.(); defaults.setEasyMode(value); }}
            slotProps={{ input: { role: "switch", "aria-describedby": "easy-mode-help" } }} />}
          label={<Typography variant="body2" fontWeight={650}>Easy Mode</Typography>} />
        <Typography id="easy-mode-help" variant="body2" color="text.secondary" sx={{ mt: .5 }}>
          Turn this off if your clip has a lot of cuts.
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
      <Box>
        <FormControlLabel sx={{ m: 0, width: "100%", justifyContent: "space-between" }} labelPlacement="start"
          control={<Switch checked={defaults.silent} disabled={disabled} onChange={(_, value) => { onChange?.(); defaults.setSilent(value); }}
            slotProps={{ input: { role: "switch", "aria-describedby": "silent-submit-help" } }} />}
          label={<Typography variant="body2" fontWeight={650}>Submit without audio</Typography>} />
        <Typography id="silent-submit-help" variant="body2" color="text.secondary" sx={{ mt: .5 }}>
          Try this if characters have trouble saying their lines. Sends MiniMax a silent clip and restores your original soundtrack after generation.
        </Typography>
      </Box>
      {watermarkOffer && <Box>
        <FormControlLabel sx={{ m: 0, width: "100%", justifyContent: "space-between" }} labelPlacement="start"
          control={<Switch checked={defaults.watermark} disabled={disabled} onChange={(_, value) => { onChange?.(); defaults.setWatermark(value); }}
            slotProps={{ input: { role: "switch", "aria-describedby": "watermark-help" } }} />}
          label={<Typography variant="body2" fontWeight={650}>Add RecastFX watermark · earn {watermarkOffer.bonusCredits} credits</Typography>} />
        <Typography id="watermark-help" variant="body2" color="text.secondary" sx={{ mt: .5 }}>
          Bonus credits are added after your finished video is saved. One bonus per paid recast.
        </Typography>
      </Box>}
      {!easyMode && <Typography variant="caption" color="text.secondary">You’ll check scene matches before generation.</Typography>}
    </Stack>
  );
}
