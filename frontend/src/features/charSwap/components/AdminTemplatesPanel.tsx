import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  FormControlLabel,
  LinearProgress,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  loadAdminTemplates,
  saveTemplate,
  saveTemplateCategory,
  trackTemplate,
  uploadTemplate,
} from "../services/templates";
import type { TemplateCatalog, TemplateOption } from "../services/templates";
import { AdminTemplateEditor } from "./AdminTemplateEditor";
import { createLocalId } from "../utils/createLocalId";
import { MAX_SOURCE_BYTES } from "../limits";

export function AdminTemplatesPanel({ canEdit }: { canEdit: boolean }) {
  const [catalog, setCatalog] = useState<TemplateCatalog | null>(null);
  const [selected, setSelected] = useState<TemplateOption | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const [categoryName, setCategoryName] = useState("");
  const [categoryEdit, setCategoryEdit] = useState<string | null>(null);
  const [file, setFile] = useState<{ file: File; id: string } | null>(null);
  const [title, setTitle] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [rights, setRights] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let active = true;
    void loadAdminTemplates()
      .then((result) => {
        if (active) {
          setCatalog(result);
          setError("");
        }
      })
      .catch((cause) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "Templates could not be loaded.",
          );
      });
    return () => {
      active = false;
    };
  }, [revision]);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The template could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  }
  const uploadCategory = categoryId || catalog?.categories[0]?.id || "";
  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="body2" color="text.secondary">
          Upload a clip, check the person, then publish. A saved selection opens
          directly at Character.
        </Typography>
      </Box>
      {error && (
        <Alert
          severity="error"
          action={
            <Button
              color="inherit"
              size="small"
              disabled={busy}
              onClick={() => setRevision((value) => value + 1)}
            >
              Refresh
            </Button>
          }
        >
          {error}
        </Alert>
      )}
      {notice && (
        <Alert severity="success" role="status">
          {notice}
        </Alert>
      )}
      {(busy || (!catalog && !error)) && (
        <LinearProgress
          aria-label={busy ? "Saving template" : "Loading templates"}
        />
      )}
      {catalog && (
        <>
          <Box
            component="section"
            aria-label="Template categories"
            sx={{ p: 2.5, bgcolor: "action.hover", borderRadius: "6px" }}
          >
            <Typography
              component="h3"
              variant="subtitle1"
              fontWeight={700}
              sx={{ mb: 2 }}
            >
              Categories
            </Typography>
            <Stack direction="row" gap={1} flexWrap="wrap" sx={{ mb: 2 }}>
              {catalog.categories.map((item) => (
                <Chip
                  key={item.id}
                  label={item.name}
                  disabled={busy}
                  onClick={
                    canEdit
                      ? () => {
                          setCategoryEdit(item.id);
                          setCategoryName(item.name);
                        }
                      : undefined
                  }
                  onDelete={
                    canEdit
                      ? () =>
                          void run(async () => {
                            setCatalog(
                              await saveTemplateCategory({
                                revision: catalog.revision,
                                category_id: item.id,
                                remove: true,
                              }),
                            );
                            setCategoryEdit(null);
                            setCategoryName("");
                          })
                      : undefined
                  }
                />
              ))}
            </Stack>
            {canEdit && (
              <Box
                component="form"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!categoryName.trim()) return;
                  void run(async () => {
                    setCatalog(
                      await saveTemplateCategory({
                        revision: catalog.revision,
                        name: categoryName.trim(),
                        ...(categoryEdit ? { category_id: categoryEdit } : {}),
                      }),
                    );
                    setCategoryName("");
                    setCategoryEdit(null);
                  });
                }}
              >
                <Stack direction={{ xs: "column", sm: "row" }} gap={1.5}>
                  <TextField
                    size="small"
                    label="Category name"
                    value={categoryName}
                    disabled={busy}
                    onChange={(event) => setCategoryName(event.target.value)}
                    slotProps={{ htmlInput: { maxLength: 40 } }}
                  />
                  <Button
                    type="submit"
                    variant="outlined"
                    disabled={busy || !categoryName.trim()}
                  >
                    {categoryEdit ? "Save category" : "Add category"}
                  </Button>
                  {categoryEdit && (
                    <Button
                      onClick={() => {
                        setCategoryEdit(null);
                        setCategoryName("");
                      }}
                    >
                      Cancel
                    </Button>
                  )}
                </Stack>
              </Box>
            )}
          </Box>
          {canEdit && (
            <Stack
              component="section"
              aria-label="Upload template"
              spacing={2}
              sx={{
                p: 2.5,
                border: "1px dashed",
                borderColor: "divider",
                borderRadius: "6px",
              }}
            >
              <Typography component="h3" variant="subtitle1" fontWeight={700}>
                New template
              </Typography>
              <input
                ref={input}
                type="file"
                accept="video/*,.mp4,.mov,.webm"
                hidden
                aria-label="Template video"
                disabled={busy}
                onChange={(event) => {
                  const chosen = event.target.files?.[0];
                  event.target.value = "";
                  if (!chosen) return;
                  if (chosen.size > MAX_SOURCE_BYTES) {
                    setError("Choose a video smaller than 100 MiB.");
                    return;
                  }
                  setFile({
                    file: chosen,
                    id: createLocalId().replace(/-/g, ""),
                  });
                  setTitle(chosen.name.replace(/\.[^.]+$/, "").slice(0, 80));
                  setRights(false);
                }}
              />
              <Stack direction="row" alignItems="center" gap={2}>
                <Button
                  variant="outlined"
                  disabled={busy}
                  onClick={() => input.current?.click()}
                >
                  {file ? "Change video" : "Choose video"}
                </Button>
                <Typography variant="body2" color="text.secondary">
                  {file ? file.file.name : "4–60 seconds · up to 100 MiB"}
                </Typography>
              </Stack>
              <Typography variant="caption" color="text.secondary">
                Templates use the first 15 seconds at most. Shorter clips keep
                their whole-second length.
              </Typography>
              <Stack direction={{ xs: "column", sm: "row" }} gap={2}>
                <TextField
                  fullWidth
                  label="New template title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  disabled={busy}
                  slotProps={{ htmlInput: { maxLength: 80 } }}
                />
                <TextField
                  select
                  fullWidth
                  label="New template category"
                  value={uploadCategory}
                  onChange={(event) => setCategoryId(event.target.value)}
                  disabled={busy || !catalog.categories.length}
                >
                  {catalog.categories.map((item) => (
                    <MenuItem key={item.id} value={item.id}>
                      {item.name}
                    </MenuItem>
                  ))}
                </TextField>
              </Stack>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={rights}
                    disabled={busy}
                    onChange={(_, checked) => setRights(checked)}
                  />
                }
                label="I have permission to publish this clip and the people shown as a reusable template."
              />
              <Button
                variant="contained"
                sx={{ alignSelf: "flex-start" }}
                disabled={
                  busy || !file || !rights || !title.trim() || !uploadCategory
                }
                onClick={() =>
                  void run(async () => {
                    if (!file) return;
                    const created = await uploadTemplate(
                      file.file,
                      title.trim(),
                      uploadCategory,
                      file.id,
                    );
                    setSelected(created);
                    setCatalog(await loadAdminTemplates());
                    setFile(null);
                    setRights(false);
                    setTitle("");
                    setNotice(
                      "Draft ready. Review the person selection before publishing.",
                    );
                  })
                }
              >
                {busy ? "Preparing template…" : "Upload and prepare"}
              </Button>
            </Stack>
          )}
          {selected && (
            <AdminTemplateEditor
              key={selected.id + ":" + selected.revision}
              template={selected}
              categories={catalog.categories}
              disabled={busy}
              canEdit={canEdit}
              onClose={() => setSelected(null)}
              onTrack={(item) =>
                void run(async () => {
                  const tracked = await trackTemplate(item);
                    setSelected({...tracked, title: item.title, category_id: item.category_id});
                  setCatalog(await loadAdminTemplates());
                  setNotice(
                    tracked.face_track.length > 1
                      ? "Face track ready. Play the preview and confirm the person."
                      : "Selection saved. No continuous face track was found; check the selected person manually.",
                  );
                })
              }
              onSave={(item, status, reviewed) =>
                void run(async () => {
                  const result = await saveTemplate(item, status, reviewed);
                  setCatalog(result);
                  setSelected(
                    result.templates.find((row) => row.id === item.id) ?? null,
                  );
                  setNotice(
                    status === "published"
                      ? "Template published."
                      : status === "archived"
                        ? "Template archived."
                        : "Draft saved.",
                  );
                })
              }
            />
          )}
          <Box>
            <Typography
              component="h3"
              variant="subtitle1"
              fontWeight={700}
              sx={{ mb: 2 }}
            >
              Your templates · {catalog.templates.length}
            </Typography>
            {!catalog.templates.length ? (
              <Typography variant="body2" color="text.secondary">
                Upload your first clip to build the collection.
              </Typography>
            ) : (
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: {
                    xs: "repeat(2,minmax(0,1fr))",
                    sm: "repeat(3,minmax(0,1fr))",
                    md: "repeat(5,minmax(0,1fr))",
                  },
                  gap: 2,
                }}
              >
                {catalog.templates.map((item) => (
                  <Button
                    key={item.id}
                    disabled={busy}
                    onClick={() => setSelected(item)}
                    aria-label={"Edit template: " + item.title}
                    sx={{
                      p: 0,
                      display: "block",
                      textAlign: "left",
                      color: "text.primary",
                      overflow: "hidden",
                      border: "1px solid",
                      borderColor:
                        selected?.id === item.id ? "primary.main" : "divider",
                      borderRadius: "6px",
                    }}
                  >
                    <Box
                      component="img"
                      src={item.poster}
                      alt=""
                      loading="lazy"
                      sx={{
                        width: "100%",
                        aspectRatio: "4/3",
                        objectFit: "cover",
                        display: "block",
                      }}
                    />
                    <Box sx={{ p: 1.5 }}>
                      <Typography variant="body2" fontWeight={650}>
                        {item.title}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {item.status === "published" ? "Published" : "Draft"} ·{" "}
                        {item.category}
                      </Typography>
                    </Box>
                  </Button>
                ))}
              </Box>
            )}
          </Box>
        </>
      )}
    </Stack>
  );
}
