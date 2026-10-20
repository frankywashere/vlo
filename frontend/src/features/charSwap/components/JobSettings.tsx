import { Box, Typography } from "@mui/material";
import type { JobSettingsUsed } from "../services/livePipeline";

export function JobSettings({ settings, creditsUsed, creditsReturned, accessMode }: {
  settings?: JobSettingsUsed; creditsUsed?: number; creditsReturned?: number; accessMode?: string;
}) {
  const rows: [string, string][] = [];
  if (creditsUsed !== undefined) rows.push(["Credits used", creditsUsed.toLocaleString()]);
  if (creditsReturned) rows.push(["Credits refunded", creditsReturned.toLocaleString()]);
  const seconds = (value: number) => `${Number(value.toFixed(2))}s`;
  if (settings) {
    if (settings.trim?.length === 2) rows.push(["Source section", `${seconds(settings.trim[0])}–${seconds(settings.trim[1])}`]);
    if (settings.duration !== undefined) rows.push(["Clip length", seconds(settings.duration)]);
    if (settings.depth_guidance && settings.depth_overlay_opacity !== undefined)
      rows.push(["Depth tint opacity", `${Math.round(settings.depth_overlay_opacity * 100)}%`]);
    for (const [label, value] of [
      ["Easy mode", settings.easy_mode],
      ["Enhanced masking", settings.enhanced_masking],
      ["Depth guidance", settings.depth_guidance],
      ["Face mesh", settings.face_mesh],
      ["Audio processing", settings.audio_processing],
      ["Submit without audio", settings.submit_without_audio],
      ["RecastFX watermark", settings.add_watermark],
    ] as const) if (value !== undefined) rows.push([label, value ? "On" : "Off"]);
    if (settings.replace_all_people) rows.push(["People", "Replace everyone"]);
    else if (settings.people_count !== undefined) rows.push(["People selected", String(settings.people_count)]);
    if (settings.reference_count !== undefined) rows.push(["References", String(settings.reference_count)]);
    const styles: Record<string, string> = { depth: "Depth guidance", depth_multi: "Depth guidance", cyan_overlay: "Cyan overlay", gray: "Gray silhouette", solid_multi: "Flat colored masks" };
    if (settings.mask_style && styles[settings.mask_style]) rows.push(["Mask method", styles[settings.mask_style]]);
    if (settings.prep_width && settings.prep_height) rows.push(["Prepared video", `${settings.prep_width} × ${settings.prep_height}${settings.prep_fps ? ` · ${settings.prep_fps} fps` : ""}`]);
    if (settings.model) rows.push(["Model", settings.model]);
    if (settings.requested_resolution) rows.push(["Render quality", settings.requested_resolution]);
    if (settings.width && settings.height) rows.push(["Video dimensions", `${settings.width} × ${settings.height}`]);
  }
  const incomplete = !settings || [settings.easy_mode, settings.enhanced_masking, settings.audio_processing].some(value => value === undefined);
  return (
    <Box component="details" sx={{ mt: 2, borderTop: 1, borderColor: "divider", pt: 1.5 }}>
      <Box component="summary" sx={{ cursor: "pointer", typography: "body2", fontWeight: 600, py: 0.5 }}>Settings used</Box>
      <Box component="dl" sx={{ my: 1, display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 1 }}>
        {rows.map(([label, value]) => (
          <Box key={label} sx={{ display: "contents" }}>
            <Typography component="dt" variant="caption" color="text.secondary">{label}</Typography>
            <Typography component="dd" variant="caption" sx={{ m: 0, overflowWrap: "anywhere" }}>{value}</Typography>
          </Box>
        ))}
      </Box>
      {accessMode === "api-key" && <Typography variant="caption" color="text.secondary">Provider API key used; no site credits charged.</Typography>}
      {incomplete && <Typography variant="caption" color="text.secondary">Some settings weren’t saved for this older clip.</Typography>}
    </Box>
  );
}
