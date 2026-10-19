import { useShallow } from "zustand/react/shallow";
import {
  Alert,
  Avatar,
  Box,
  Button,
  Chip,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CheckRounded from "@mui/icons-material/CheckRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import { useCharSwapStore } from "../useCharSwapStore";
import type { TargetPerson } from "../model/model";
import type { DemoManifest, SamplePerson } from "../services/demo";
import { boxAtTime } from "../services/demo";
import { captureAnchor } from "../services/localMedia";
import type { NormalizedPoint } from "../utils/videoGeometry";
import { createLocalId } from "../utils/createLocalId";
import { VideoPreview } from "./VideoPreview";
import { PANEL } from "./stepStyles";
import { MAX_TARGET_PEOPLE, TARGET_COLORS } from "../limits";

interface PeopleStepProps {
  demo: DemoManifest | null;
  maxPeople?: number;
}
export function PeopleStep({ demo, maxPeople = MAX_TARGET_PEOPLE }: PeopleStepProps) {
  const { clip, trim, targets, replaceAllPeople, setReplaceAllPeople, selectTarget, renameTarget, setError } =
    useCharSwapStore(
      useShallow((s) => ({
        clip: s.clip,
        trim: s.trim,
        targets: s.targets,
        replaceAllPeople: s.replaceAllPeople,
        setReplaceAllPeople: s.setReplaceAllPeople,
        selectTarget: s.selectTarget,
        renameTarget: s.renameTarget,
        setError: s.setError,
      })),
    );
  if (!clip) return null;
  const sample = clip.mode === "demo" ? (demo?.people ?? []) : [];
  function choose(person: TargetPerson) {
    if (
      !replaceAllPeople && targets.length >= maxPeople &&
      !targets.some((p) => p.id === person.id)
    ) {
      setError(
        maxPeople === 1 ? "Choose one person. Remove the current selection to choose someone else."
          : `Choose up to ${maxPeople} people. Remove a selection to choose someone else.`,
      );
      return;
    }
    selectTarget(person);
  }
  function chooseSample(person: SamplePerson, time: number) {
    const box = boxAtTime(person, time);
    if (box)
      choose({
        ...person,
        anchor: { x: box.x + box.width / 2, y: box.y + box.height / 2, time },
      });
  }
  function choosePoint(point: NormalizedPoint, video: HTMLVideoElement) {
    if (sample.length) {
      const match = sample
        .filter((person) => {
          const box = boxAtTime(person, video.currentTime);
          return (
            box &&
            point.x >= box.x &&
            point.x <= box.x + box.width &&
            point.y >= box.y &&
            point.y <= box.y + box.height
          );
        })
        .sort((a, b) => {
          const left = boxAtTime(a, video.currentTime)!;
          const right = boxAtTime(b, video.currentTime)!;
          return left.width * left.height - right.width * right.height;
        })[0];
      if (match) chooseSample(match, video.currentTime);
      else
        setError("Tap inside a person's outline, or choose their card below.");
      return;
    }
    const existing = !replaceAllPeople && targets.find(
      (target) =>
        Math.hypot(target.anchor.x - point.x, target.anchor.y - point.y) <
          0.06 && Math.abs(target.anchor.time - video.currentTime) < 0.5,
    );
    if (existing) {
      selectTarget(existing);
      return;
    }
    const index =
      [1, 2, 3, 4, 5].find(
        (number) =>
          !targets.some((target) => target.name === `Person ${number}`),
      ) ?? targets.length + 1;
    choose({
      id: createLocalId(),
      name: `Person ${index}`,
      color: TARGET_COLORS[targets.length % TARGET_COLORS.length],
      anchor: { ...point, time: video.currentTime },
      thumbnail: captureAnchor(video, point),
    });
  }
  return (
    <Stack spacing={2.5}>
      {maxPeople > 1 && <Box sx={PANEL}>
        <Button variant={replaceAllPeople ? "contained" : "outlined"}
          aria-pressed={Boolean(replaceAllPeople)}
          onClick={() => setReplaceAllPeople(!replaceAllPeople)}
          sx={{minHeight: 48, width: {xs: "100%", sm: "auto"}}}>
          Replace all people
        </Button>
        <Typography variant="body2" color="text.secondary" sx={{mt: 1}}>
          Use one reference image for everyone in every scene. Each person becomes a separate version of that character.
          {replaceAllPeople ? " Tap a person below to switch back to individual selections." : " Or tap up to five people below and give each their own replacement."}
        </Typography>
      </Box>}
      {maxPeople === 1 && <Alert severity="info">Select one person and one reference image. Multi-characters is currently off.</Alert>}
      {maxPeople === 1 && (targets.length > 1 || replaceAllPeople) && <Alert severity="warning">Remove extra selections before continuing. Your existing selections have been kept.</Alert>}
      <VideoPreview
        clip={clip}
        trim={trim}
        selectable
        onPoint={choosePoint}
        poster={clip.mode === "demo" ? demo?.poster : undefined}
        overlay={(time) => (
          <>
            {sample.map((person) => {
              const box = boxAtTime(person, time);
              const selectedTarget = targets.find((target) => target.id === person.id);
              const selected = Boolean(selectedTarget);
              const color = selectedTarget?.color ?? person.color;
              return (
                box && (
                  <Box
                    component="button"
                    key={person.id}
                    aria-label={`${selected ? "Deselect" : "Select"} ${person.name}`}
                    aria-pressed={selected}
                    onClick={() => chooseSample(person, time)}
                    sx={{
                      position: "absolute",
                      left: `${box.x * 100}%`,
                      top: `${box.y * 100}%`,
                      width: `${box.width * 100}%`,
                      height: `${box.height * 100}%`,
                      border: `${selected ? 3 : 1}px solid ${selected ? color : "#ffffff70"}`,
                      borderRadius: 1.5,
                      bgcolor: selected ? `${color}12` : "transparent",
                      cursor: "pointer",
                      minWidth: 44,
                      minHeight: 44,
                      transition: "border-color .15s",
                    }}
                  >
                    <Box
                      component="span"
                      sx={{
                        position: "absolute",
                        top: 6,
                        left: 6,
                        bgcolor: selected ? color : "#121212dd",
                        color: selected ? "#102133" : "white",
                        borderRadius: 0.75,
                        px: 1,
                        py: 0.6,
                        fontSize: 12,
                        fontWeight: 650,
                        whiteSpace: "nowrap",
                      }}
                    >
                      {selected && "✓ "}
                      {person.name}
                    </Box>
                  </Box>
                )
              );
            })}
            {!sample.length && !replaceAllPeople &&
              targets
                .filter((target) => Math.abs(target.anchor.time - time) < 0.5)
                .map((target) => (
                  <Box
                    key={target.id}
                    component="button"
                    onClick={() => choose(target)}
                    aria-label={`Deselect ${target.name}`}
                    sx={{
                      position: "absolute",
                      left: `${target.anchor.x * 100}%`,
                      top: `${target.anchor.y * 100}%`,
                      transform: "translate(-50%, -50%)",
                      width: 44,
                      height: 44,
                      border: `2px solid ${target.color}`,
                      borderRadius: "50%",
                      bgcolor: "#142437aa",
                      color: target.color,
                      cursor: "pointer",
                      fontSize: 20,
                    }}
                  >
                    <CheckRounded />
                  </Box>
                ))}
          </>
        )}
      />
      {!!sample.length && (
        <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
          {sample.map((person) => (
            <Button
              key={person.id}
              variant={
                targets.some((p) => p.id === person.id)
                  ? "contained"
                  : "outlined"
              }
              onClick={() => chooseSample(person, trim[0])}
              startIcon={
                <Avatar
                  src={person.thumbnail}
                  variant="rounded"
                  sx={{
                    width: 32,
                    height: 32,
                    "& img": { objectPosition: "center top" },
                  }}
                />
              }
              sx={{ minHeight: 48 }}
            >
              {person.name}
            </Button>
          ))}
        </Stack>
      )}
      <Box sx={PANEL}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mb: targets.length ? 2 : 0 }}
        >
          <Typography fontWeight={600}>People to replace</Typography>
          <Chip size="small" label={replaceAllPeople ? "Everyone" : `${targets.length} of ${maxPeople} selected`} />
        </Stack>
        {!targets.length && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Pause on a clear frame, then tap a person. Everyone else stays in
            the shot.
          </Typography>
        )}
        <Stack spacing={2}>
          {replaceAllPeople ? <Typography>Everyone in the clip will use the same replacement image.</Typography> : targets.map((person) => (
            <Stack
              key={person.id}
              direction="row"
              alignItems="center"
              spacing={1.5}
            >
              <Avatar
                src={person.thumbnail}
                variant="rounded"
                sx={{
                  border: `2px solid ${person.color}`,
                  width: 48,
                  height: 48,
                  "& img": { objectPosition: "center top" },
                }}
              />
              <TextField
                label="Person label"
                size="small"
                defaultValue={person.name}
                key={`${person.id}-${person.name}`}
                onBlur={(event) => renameTarget(person.id, event.target.value)}
                slotProps={{
                  htmlInput: {
                    maxLength: 40,
                    "aria-label": `Rename ${person.name}`,
                  },
                }}
                sx={{ flex: 1 }}
              />
              <IconButton
                aria-label={`Remove ${person.name}`}
                sx={{ width: 44, height: 44 }}
                onClick={() => choose(person)}
              >
                <CloseRounded fontSize="small" />
              </IconButton>
            </Stack>
          ))}
        </Stack>
      </Box>
      {clip.mode === "upload" && maxPeople > 1 && !replaceAllPeople && (
        <Alert severity="info" role="note">
          Tap each person once, on a clear frame. Each selection gets its own replacement image; everyone else stays in the shot.
        </Alert>
      )}
      <Typography variant="body2" color="text.secondary">
        {replaceAllPeople ? "You’ll choose one replacement image for everyone next." : "You’ll choose a replacement image for each person next."}
      </Typography>
    </Stack>
  );
}
