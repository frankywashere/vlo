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
import type { ReferenceAsset, TargetPerson } from "../model/model";
import type { DemoManifest } from "../services/demo";
import { PersonBadge } from "./PersonBadge";
import { ReferenceUpload } from "./ReferenceUpload";
import { PANEL } from "./stepStyles";

interface ReferencesStepProps {
  demo: DemoManifest | null;
  onImport: (id: string, file: File) => void;
  pendingIds: string[];
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
}: ReferenceCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const pending = pendingIds.includes(person.id);
  return (
    <Box sx={{ ...PANEL, borderLeft: `3px solid ${person.color}` }}>
      <PersonBadge person={person} subtitle="Original person" />
      <Divider sx={{ my: 2 }} />
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "150px 1fr" },
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
            height: 164,
            minWidth: 44,
            minHeight: 44,
            borderRadius: 1.5,
            bgcolor: "#14171c",
            border: "1px dashed #49515d",
            display: "grid",
            placeItems: "center",
            overflow: "hidden",
            transition: "border-color .15s, background-color .15s",
            "&:hover": { borderColor: "#75baff", bgcolor: "#1c2938" },
            "&.Mui-focusVisible": {
              outline: "2px solid #a3d1ff",
              outlineOffset: 3,
            },
            "&.Mui-disabled": { opacity: 0.6 },
          }}
        >
          {reference ? (
            <Box
              component="img"
              src={reference.url}
              alt={`${person.name} replacement: ${reference.name}`}
              sx={{ width: "100%", height: "100%", objectFit: "contain" }}
            />
          ) : (
            <AddPhotoAlternateOutlined
              sx={{ color: "#768497", fontSize: 38 }}
            />
          )}
        </ButtonBase>
        <Stack spacing={1.25} alignItems="flex-start" sx={{ minWidth: 0 }}>
          <Typography fontWeight={600}>
            {reference ? reference.name : `Who replaces ${person.name}?`}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Use one clear image showing the character’s face and look.
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
          {demo && (
            <Button
              size="small"
              disabled={pending}
              onClick={() => assignReference(person.id, demo.reference)}
            >
              Use sample teddy bear
            </Button>
          )}
          {!reference && references.length > 0 && (
            <Stack direction="row" flexWrap="wrap" gap={1}>
              {references.map((item) => (
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
          sx={{ mt: 2, color: "#8acdb1" }}
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
          onImport={onImport}
          assignReference={assignReference}
          clearReference={clearReference}
        />
      ))}
      <Typography variant="body2" color="text.secondary">
        Each card connects one original person to one replacement. You can use
        the same reference more than once.
      </Typography>
    </Stack>
  );
}
