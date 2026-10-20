import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { VideoPreview } from "./VideoPreview";
import type { TemplateCatalog, TemplateOption } from "../services/templates";

interface AdminTemplateEditorProps {
  template: TemplateOption;
  categories: TemplateCatalog["categories"];
  disabled: boolean;
  canEdit: boolean;
  onSave: (
    item: TemplateOption,
    status: "draft" | "published" | "archived",
    reviewed: boolean,
  ) => void;
  onTrack: (item: TemplateOption) => void;
  onClose: () => void;
}
export function AdminTemplateEditor({
  template,
  categories,
  disabled,
  canEdit,
  onSave,
  onTrack,
  onClose,
}: AdminTemplateEditorProps) {
  const [draft, setDraft] = useState(template);
  const [reviewed, setReviewed] = useState(template.status === "published");
  const [archiveOpen, setArchiveOpen] = useState(false);
  const published = template.status === "published";
  const clip = {
    id: template.id,
    name: template.title,
    url: template.video,
    duration: template.duration!,
    width: template.width!,
    height: template.height!,
    mode: "upload" as const,
  };
  return (
    <Stack
      component="section"
      aria-label="Template editor"
      spacing={2.5}
      sx={{
        p: { xs: 2, sm: 3 },
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
      }}
    >
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        gap={2}
      >
        <Box>
          <Typography component="h3" variant="h6">
            {template.title}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {published ? "Published" : "Draft"} · {template.duration}s
          </Typography>
        </Box>
        <Button disabled={disabled} onClick={onClose}>
          Close
        </Button>
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", md: "minmax(0,1fr) minmax(0,1fr)" },
          gap: 3,
        }}
      >
        <Stack spacing={1.5} sx={{ minWidth: 0 }}>
          <VideoPreview
            clip={clip}
            trim={[0, template.duration!]}
            poster={template.poster}
            selectable={canEdit && !disabled && !published}
            maxPreviewHeight={280}
            onPoint={(point, video) => {
              setDraft((current) => ({
                ...current,
                anchor: { ...point, time: video.currentTime },
                face_track: [],
                tracking: "selected",
              }));
              setReviewed(false);
            }}
            overlay={(time) => {
              const frame = draft.face_track.find(
                (item) => Math.abs(item.time - time) <= 0.13,
              );
              const anchor = draft.anchor;
              return frame ? (
                <Box
                  sx={{
                    position: "absolute",
                    left: `${frame.x * 100}%`,
                    top: `${frame.y * 100}%`,
                    width: `${frame.width * 100}%`,
                    height: `${frame.height * 100}%`,
                    border: "2px solid",
                    borderColor: "primary.main",
                    borderRadius: "5px",
                    boxShadow: "0 0 0 1px #0006",
                  }}
                />
              ) : anchor && Math.abs(anchor.time - time) < 0.15 ? (
                <Box
                  sx={{
                    position: "absolute",
                    left: `${anchor.x * 100}%`,
                    top: `${anchor.y * 100}%`,
                    width: 22,
                    height: 22,
                    borderRadius: "50%",
                    transform: "translate(-50%,-50%)",
                    border: "3px solid white",
                    bgcolor: "primary.main",
                    boxShadow: "0 0 0 1px #0006",
                  }}
                />
              ) : null;
            }}
          />
          <Typography variant="body2" color="text.secondary">
            {published
              ? "Unpublish to change the person selection."
              : "Pause on a clear face and tap the person to replace."}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {draft.face_track.length > 1
              ? `Face tracked in ${draft.face_track.length} frames. Review the clip before publishing.`
              : draft.anchor
                ? `Person selected at ${draft.anchor.time.toFixed(2)}s. You can track their face or keep this selection.`
                : "No person selected. This template will start at Clip."}
          </Typography>
          {canEdit && !published && (
            <Stack direction="row" gap={1} flexWrap="wrap">
              <Button
                variant="outlined"
                disabled={disabled || !draft.anchor}
                onClick={() => onTrack(draft)}
              >
                Track selected face
              </Button>
              <Button
                disabled={disabled || !draft.anchor}
                onClick={() => {
                  setDraft((current) => ({
                    ...current,
                    anchor: null,
                    face_track: [],
                    tracking: "none",
                  }));
                  setReviewed(false);
                }}
              >
                Clear selection
              </Button>
            </Stack>
          )}
        </Stack>
        <Stack spacing={2} sx={{ minWidth: 0 }}>
          <TextField
            label="Template title"
            value={draft.title}
            disabled={disabled || !canEdit}
            onChange={(event) =>
              setDraft((current) => ({ ...current, title: event.target.value }))
            }
            slotProps={{ htmlInput: { maxLength: 80 } }}
          />
          <TextField
            select
            label="Template category"
            value={draft.category_id}
            disabled={disabled || !canEdit}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                category_id: event.target.value,
              }))
            }
          >
            {categories.map((item) => (
              <MenuItem key={item.id} value={item.id}>
                {item.name}
              </MenuItem>
            ))}
          </TextField>
          <FormControlLabel
            control={
              <Switch
                checked={Boolean(draft.anchor)}
                disabled={disabled || !canEdit || published || !draft.anchor}
                onChange={(_, checked) => {
                  if (!checked)
                    setDraft((current) => ({
                      ...current,
                      anchor: null,
                      face_track: [],
                      tracking: "none",
                    }));
                }}
              />
            }
            label="Start users at Character"
          />
          <Typography variant="body2" color="text.secondary">
            {draft.anchor
              ? "The clip and selected person are filled in. Users choose their character next."
              : "Users choose their moment and person before adding a character."}
          </Typography>
          {draft.anchor && (
            <FormControlLabel
              control={
                <Checkbox
                  checked={reviewed}
                  disabled={disabled || !canEdit}
                  onChange={(_, checked) => setReviewed(checked)}
                />
              }
              label="I’ve reviewed the person selection."
            />
          )}
          {draft.anchor && draft.face_track.length < 2 && (
            <Alert severity="info">
              A person is selected. Automatic face tracking did not find a
              continuous track; review your selection before publishing.
            </Alert>
          )}
          {canEdit && (
            <Stack direction="row" gap={1.5} flexWrap="wrap">
              <Button
                variant="contained"
                disabled={
                  disabled ||
                  !draft.title.trim() ||
                  !draft.category_id ||
                  (Boolean(draft.anchor) && !reviewed)
                }
                onClick={() => onSave(draft, "published", reviewed)}
              >
                {published ? "Save changes" : "Publish template"}
              </Button>
              <Button
                variant="outlined"
                disabled={disabled || !draft.title.trim()}
                onClick={() => onSave(draft, "draft", reviewed)}
              >
                {published ? "Unpublish" : "Save draft"}
              </Button>
              <Button
                color="inherit"
                disabled={disabled}
                onClick={() => setArchiveOpen(true)}
              >
                Archive
              </Button>
            </Stack>
          )}
        </Stack>
      </Box>
      <Dialog open={archiveOpen} onClose={() => setArchiveOpen(false)}>
        <DialogTitle>Archive this template?</DialogTitle>
        <DialogContent>
          It will disappear from the template collection. Existing generations
          are kept.
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setArchiveOpen(false)}>Keep template</Button>
          <Button
            variant="contained"
            onClick={() => {
              setArchiveOpen(false);
              onSave(draft, "archived", reviewed);
            }}
          >
            Archive template
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
