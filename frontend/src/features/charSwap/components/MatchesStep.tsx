import { useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  Alert,
  Avatar,
  Box,
  Button,
  Chip,
  Stack,
  Typography,
} from "@mui/material";
import CheckRounded from "@mui/icons-material/CheckRounded";
import PersonOffOutlined from "@mui/icons-material/PersonOffOutlined";
import { useCharSwapStore } from "../useCharSwapStore";
import { getUnresolvedIssues } from "../model/model";
import type { DemoManifest } from "../services/demo";
import { boxAtTime } from "../services/demo";
import { formatTime } from "../utils/videoGeometry";
import { VideoPreview } from "./VideoPreview";
import { PersonBadge } from "./PersonBadge";
import { PANEL } from "./stepStyles";

interface MatchesStepProps {
  demo: DemoManifest | null;
}
export function MatchesStep({ demo }: MatchesStepProps) {
  const { clip, trim, targets, issues, decisions, trackingReady, chooseMatch } =
    useCharSwapStore(
      useShallow((s) => ({
        clip: s.clip,
        trim: s.trim,
        targets: s.targets,
        issues: s.issues,
        decisions: s.decisions,
        trackingReady: s.trackingReady,
        chooseMatch: s.chooseMatch,
      })),
    );
  const [activeId, setActiveId] = useState<string | null>(null);
  if (!clip) return null;
  if (!trackingReady)
    return (
      <Alert severity="info" role="note" sx={{ mt: 1 }}>
        Automatic tracking isn’t connected for uploaded clips yet. Your people
        and references are saved for this session. Use the sample clip to try
        the matching review.
      </Alert>
    );
  const unresolved = getUnresolvedIssues(useCharSwapStore.getState());
  const active =
    issues.find((issue) => issue.id === activeId) ?? unresolved[0] ?? issues[0];
  if (!active)
    return (
      <Box sx={PANEL}>
        <CheckRounded color="success" sx={{ fontSize: 36 }} />
        <Typography variant="h6" sx={{ mt: 1 }}>
          All people are matched
        </Typography>
        <Typography color="text.secondary" sx={{ mt: 1 }}>
          There are no sections to check. You’re ready to review the setup.
        </Typography>
      </Box>
    );
  const person = targets.find((target) => target.id === active.targetId)!;
  const decision = decisions[active.id];
  return (
    <Stack spacing={2.5}>
      <Alert severity="info" role="note">
        Sample review moment. This demonstrates the interaction; it isn’t a
        measured tracking failure.
      </Alert>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography fontWeight={600}>
          {unresolved.length
            ? `${unresolved.length} ${unresolved.length === 1 ? "section needs" : "sections need"} your help`
            : "All sections confirmed"}
        </Typography>
        <Chip
          size="small"
          label={`${issues.length - unresolved.length} / ${issues.length} checked`}
          color={unresolved.length ? "default" : "success"}
        />
      </Stack>
      <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1}>
        {issues.map((issue, index) => (
          <Button
            key={issue.id}
            size="small"
            variant={active.id === issue.id ? "contained" : "outlined"}
            onClick={() => setActiveId(issue.id)}
            startIcon={decisions[issue.id] ? <CheckRounded /> : undefined}
          >
            Section {index + 1}
          </Button>
        ))}
      </Stack>
      <Box sx={PANEL}>
        <PersonBadge
          person={person}
          subtitle={`Find this person at ${formatTime(active.start - trim[0])}–${formatTime(active.end - trim[0])}`}
        />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
          {active.reason}
        </Typography>
      </Box>
      <VideoPreview
        key={active.id}
        clip={clip}
        trim={[active.start, active.end]}
        seekTo={active.start}
        poster={demo?.poster}
        overlay={(time) => (
          <>
            {demo?.people.map((candidate) => {
              const box = boxAtTime(candidate, time);
              const selected = decision?.candidateId === candidate.id;
              return (
                box && (
                  <Box
                    key={candidate.id}
                    component="button"
                    aria-label={`Match ${candidate.name} to ${person.name}`}
                    aria-pressed={selected}
                    onClick={() => chooseMatch(active.id, candidate.id)}
                    sx={{
                      position: "absolute",
                      left: `${box.x * 100}%`,
                      top: `${box.y * 100}%`,
                      width: `${box.width * 100}%`,
                      height: `${box.height * 100}%`,
                      border: `2px solid ${selected ? person.color : "#ffffff90"}`,
                      bgcolor: selected ? `${person.color}22` : "transparent",
                      borderRadius: 1,
                      cursor: "pointer",
                      minWidth: 44,
                      minHeight: 44,
                    }}
                  >
                    <Box
                      component="span"
                      sx={{
                        position: "absolute",
                        left: 4,
                        top: 4,
                        p: 0.75,
                        borderRadius: 1,
                        bgcolor: selected ? person.color : "#111d",
                        color: selected ? "#112" : "#fff",
                        fontSize: 12,
                      }}
                    >
                      {candidate.name}
                    </Box>
                  </Box>
                )
              );
            })}
          </>
        )}
      />
      <Box>
        <Typography fontWeight={600} sx={{ mb: 1.5 }}>
          Which person is {person.name} here?
        </Typography>
        <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
          {active.candidates.map((candidate) => (
            <Button
              key={candidate.id}
              variant={
                decision?.candidateId === candidate.id
                  ? "contained"
                  : "outlined"
              }
              onClick={() => chooseMatch(active.id, candidate.id)}
              startIcon={
                <Avatar
                  src={candidate.thumbnail}
                  variant="rounded"
                  sx={{
                    width: 36,
                    height: 36,
                    "& img": { objectPosition: "center top" },
                  }}
                />
              }
              sx={{ minHeight: 52 }}
            >
              {candidate.name}
            </Button>
          ))}
          <Button
            variant={decision?.candidateId === null ? "contained" : "outlined"}
            onClick={() => chooseMatch(active.id, null)}
            startIcon={<PersonOffOutlined />}
            sx={{ minHeight: 52 }}
          >
            Not visible here
          </Button>
        </Stack>
      </Box>
      {decision && (
        <Typography variant="body2" sx={{ color: "#8acdb1" }}>
          {decision.candidateId === null
            ? "This person won’t be replaced in this section."
            : "Match confirmed for this section."}
        </Typography>
      )}
    </Stack>
  );
}
