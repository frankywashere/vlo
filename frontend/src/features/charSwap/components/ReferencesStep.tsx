import { useRef } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  Box,
  Button,
  ButtonBase,
  Divider,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import AddPhotoAlternateOutlined from "@mui/icons-material/AddPhotoAlternateOutlined";
import CheckRounded from "@mui/icons-material/CheckRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import { useCharSwapStore } from "../useCharSwapStore";
import { ALL_PEOPLE_TARGET_ID, type ReferenceAsset, type TargetPerson } from "../model/model";
import type { DemoManifest } from "../services/demo";
import { PersonBadge } from "./PersonBadge";
import { useFileDrop } from "../hooks/useFileDrop";
import { ReferenceUpload } from "./ReferenceUpload";
import { PANEL } from "./stepStyles";
import { ReferenceBackground } from "./ReferenceBackground";

interface ReferencesStepProps {
  demo: DemoManifest | null;
  onImport: (id: string, file: File) => void;
  pendingIds: string[];
  cleanupEnabled?: boolean;
  cleanupEndpoint?: string;
}

interface ReferenceCardProps extends ReferencesStepProps {
  person: TargetPerson;
  reference?: ReferenceAsset;
  references: ReferenceAsset[];
  assignReference: (id: string, reference: ReferenceAsset) => void;
  clearReference: (id: string) => void;
}

function ReferenceCard({
  person,
  reference,
  references,
  demo,
  pendingIds,
  onImport,
  assignReference,
  clearReference,
  cleanupEnabled,
  cleanupEndpoint,
}: ReferenceCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const pending = pendingIds.includes(person.id);
  const {dragging,dropHandlers} = useFileDrop(file => onImport(person.id,file),pending);
  return (
    <Box role="group" aria-label={`Reference upload area for ${person.name}`} {...dropHandlers}
      sx={{ ...PANEL, borderLeft: `3px solid ${person.color}`, outline:dragging?"2px dashed":"none",outlineColor:"primary.main",outlineOffset:3 }}>
      <PersonBadge person={person} subtitle={person.id === ALL_PEOPLE_TARGET_ID ? "One reference for everyone in every scene" : "Original person"} />
      <Divider sx={{ my: 2 }} />
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "220px 1fr" },
          gap: 2.5,
          alignItems: "center",
        }}
      >
        <ButtonBase
          component="button"
          type="button"
          disabled={pending}
          onClick={() => {
            if (!pending) inputRef.current?.click();
          }}
          aria-label={`${reference ? "Change" : "Upload"} reference image for ${person.name}`}
          sx={{
            width: "100%",
            height: 220,
            minWidth: 44,
            minHeight: 44,
            borderRadius: "5px",
            bgcolor: "background.default",
            border: "1px dashed",
            borderColor: "divider",
            display: "grid",
            placeItems: "center",
            overflow: "hidden",
            transition: "border-color .15s, background-color .15s",
            "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
            "&.Mui-focusVisible": {
              outline: "2px solid #cc3f37",
              outlineOffset: 3,
            },
            "&.Mui-disabled": { opacity: 0.6 },
          }}
        >
          {reference ? (
            <Box
              component="img"
              src={reference.url}
              draggable={false}
              alt={`${person.name} replacement: ${reference.name}`}
              sx={{ width: "100%", height: "100%", objectFit: "contain" }}
            />
          ) : (
            <AddPhotoAlternateOutlined
              sx={{ color: "text.secondary", fontSize: 38 }}
            />
          )}
        </ButtonBase>
        <Stack spacing={1.25} alignItems="flex-start" sx={{ minWidth: 0 }}>
          <Typography fontWeight={600}>
            {reference ? reference.name : person.id === ALL_PEOPLE_TARGET_ID ? "Your replacement character" : `Who replaces ${person.name}?`}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Drop an image here or upload one clear picture of the character.
          </Typography>
          <Stack direction="row" spacing={1}>
            <ReferenceUpload
              person={person}
              pending={pending}
              hasReference={!!reference}
              onImport={onImport}
              inputRef={inputRef}
            />
            {reference && (
              <IconButton
                aria-label={`Remove reference for ${person.name}`}
                disabled={pending}
                onClick={() => clearReference(person.id)}
                sx={{ width: 44, height: 44 }}
              >
                <CloseRounded fontSize="small" />
              </IconButton>
            )}
          </Stack>
          {cleanupEnabled && reference && <ReferenceBackground targetId={person.id} reference={reference} cleanupEndpoint={cleanupEndpoint} />}
          {demo && (
            <Button
              size="small"
              disabled={pending}
              onClick={() => assignReference(person.id, demo.reference)}
            >
              Use sample teddy bear
            </Button>
          )}
          {references.some((item) => item.id !== reference?.id && !item.cleanup?.pending) && (
            <Stack direction="row" flexWrap="wrap" gap={1}>
              {references.filter((item) => item.id !== reference?.id && !item.cleanup?.pending).map((item) => (
                <Button
                  key={item.id}
                  size="small"
                  variant="outlined"
                  disabled={pending}
                  onClick={() => assignReference(person.id, item)}
                >
                  Use {item.name}
                </Button>
              ))}
            </Stack>
          )}
        </Stack>
      </Box>
      {reference && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ mt: 2, color: "success.main" }}
        >
          <CheckRounded fontSize="small" />
          <Typography variant="body2">Assigned to {person.name}</Typography>
        </Stack>
      )}
    </Box>
  );
}

export function ReferencesStep({
  demo,
  onImport,
  pendingIds,
  cleanupEnabled = false,
  cleanupEndpoint,
}: ReferencesStepProps) {
  const { targets, references, assignments, assignReference, clearReference } =
    useCharSwapStore(
      useShallow((s) => ({
        targets: s.targets,
        references: s.references,
        assignments: s.assignments,
        assignReference: s.assignReference,
        clearReference: s.clearReference,
      })),
    );
  return (
    <Stack spacing={2.5}>
      <Typography variant="body2" color="text.secondary">
        JPG, PNG and WebP photos are converted automatically. For multi-picture JPEGs, we use the main photo.
      </Typography>
      {targets.map((person) => (
        <ReferenceCard
          key={person.id}
          person={person}
          reference={references.find(
            (item) => item.id === assignments[person.id],
          )}
          references={references}
          demo={demo}
          pendingIds={pendingIds}
          cleanupEnabled={cleanupEnabled}
          cleanupEndpoint={cleanupEndpoint}
          onImport={onImport}
          assignReference={assignReference}
          clearReference={clearReference}
        />
      ))}
      <Typography variant="body2" color="text.secondary">
        {targets[0]?.id === ALL_PEOPLE_TARGET_ID
          ? "Everyone will become a separate version of this character. Their individual positions and motion stay separate."
          : "Each card connects one original person to one replacement. You can use the same reference more than once."}
      </Typography>
    </Stack>
  );
}
