import { useShallow } from "zustand/react/shallow";
import { Alert, Avatar, Box, Button, Stack, Typography } from "@mui/material";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import DownloadRounded from "@mui/icons-material/DownloadRounded";
import { useCharSwapStore } from "../useCharSwapStore";
import { formatTime } from "../utils/videoGeometry";
import { PersonBadge } from "./PersonBadge";
import { PANEL } from "./stepStyles";

export function ReviewStep() {
  const {
    clip,
    trim,
    targets,
    references,
    assignments,
    issues,
    decisions,
    visit,
  } = useCharSwapStore(
    useShallow((s) => ({
      clip: s.clip,
      trim: s.trim,
      targets: s.targets,
      references: s.references,
      assignments: s.assignments,
      issues: s.issues,
      decisions: s.decisions,
      visit: s.visit,
    })),
  );
  if (!clip) return null;
  function saveSetup() {
    // Local object URLs and thumbnail data are deliberately excluded from the handoff.
    const setup = {
      version: 1,
      previewOnly: true,
      clip: { name: clip!.name, duration: clip!.duration, trim },
      people: targets.map((person) => ({
        id: person.id,
        name: person.name,
        anchor: person.anchor,
        reference: references.find((ref) => ref.id === assignments[person.id])
          ?.name,
      })),
      matches: issues.map((issue) => ({
        id: issue.id,
        targetId: issue.targetId,
        start: issue.start,
        end: issue.end,
        decision: decisions[issue.id],
        simulated: clip!.mode === "demo",
      })),
      output: { deliveryResolution: "480p", restoreOriginalAudio: true },
      submitted: false,
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(setup, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "charswap-setup.json";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <Stack spacing={2.5}>
      <Box sx={PANEL}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Box>
            <Typography fontWeight={600}>{clip.name}</Typography>
            <Typography variant="body2" color="text.secondary">
              {formatTime(trim[0])}–{formatTime(trim[1])} ·{" "}
              {(trim[1] - trim[0]).toFixed(1)} seconds
            </Typography>
          </Box>
          <Button size="small" onClick={() => visit("clip")}>
            Edit clip
          </Button>
        </Stack>
      </Box>
      <Box sx={PANEL}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mb: 2 }}
        >
          <Typography fontWeight={600}>Your replacements</Typography>
          <Button size="small" onClick={() => visit("references")}>
            Edit references
          </Button>
        </Stack>
        <Stack spacing={2}>
          {targets.map((person) => {
            const reference = references.find(
              (item) => item.id === assignments[person.id],
            );
            return (
              <Box
                key={person.id}
                sx={{
                  minWidth: 0,
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) 24px minmax(0, 1fr)",
                  alignItems: "center",
                  gap: 1.5,
                }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <PersonBadge person={person} compact stackedOnMobile />
                </Box>
                <ArrowForwardRounded sx={{ color: "#758293" }} />
                <Stack
                  direction={{ xs: "column", sm: "row" }}
                  alignItems="center"
                  spacing={1.5}
                  sx={{ minWidth: 0 }}
                >
                  <Avatar
                    src={reference?.url}
                    variant="rounded"
                    sx={{ width: 56, height: 64 }}
                  />
                  <Typography
                    variant="body2"
                    sx={{
                      minWidth: 0,
                      overflowWrap: "anywhere",
                      textAlign: { xs: "center", sm: "left" },
                    }}
                  >
                    {reference?.name}
                  </Typography>
                </Stack>
              </Box>
            );
          })}
        </Stack>
      </Box>
      <Box sx={PANEL}>
        <Typography fontWeight={600} sx={{ mb: 1.5 }}>
          Ready for generation
        </Typography>
        <Stack spacing={1}>
          <Typography variant="body2" color="text.secondary">
            ✓ Every selected person has a reference
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ✓{" "}
            {issues.length
              ? `${issues.length} review ${issues.length === 1 ? "section" : "sections"} confirmed`
              : "No uncertain sections"}
            {clip.mode === "demo" ? " (sample)" : ""}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ✓ Original soundtrack restored on export
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ✓ Final video delivered at 480p
          </Typography>
        </Stack>
      </Box>
      <Alert severity="info" role="note">
        This is the UI preview. Generation is not connected yet, and nothing has
        been submitted or charged.
      </Alert>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <Button variant="contained" size="large" disabled sx={{ flex: 1 }}>
          Generate replacements
        </Button>
        <Button
          variant="outlined"
          startIcon={<DownloadRounded />}
          onClick={saveSetup}
          size="large"
        >
          Save setup
        </Button>
      </Stack>
    </Stack>
  );
}
