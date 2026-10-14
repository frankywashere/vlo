import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CloseRounded from "@mui/icons-material/CloseRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import DownloadRounded from "@mui/icons-material/DownloadRounded";
import {
  deleteSavedGeneration,
  loadLibrary,
} from "../services/accounts";
import type { SavedGeneration } from "../services/accounts";

export function GenerationLibrary({
  onCreate,
  revision,
}: {
  onCreate: () => void;
  revision: number;
}) {
  const [items, setItems] = useState<SavedGeneration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<SavedGeneration | null>(null);
  const [deleting, setDeleting] = useState<SavedGeneration | null>(null);
  const [busy, setBusy] = useState(false);
  async function reload() {
    setItems(await loadLibrary());
  }
  useEffect(() => {
    let active = true;
    loadLibrary()
      .then((value) => {
        if (active) setItems(value);
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision]);
  async function remove() {
    if (!deleting) return;
    setBusy(true);
    try {
      await deleteSavedGeneration(deleting.id);
      setDeleting(null);
      await reload();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Unable to delete video.",
      );
    } finally {
      setBusy(false);
    }
  }
  const filtered = items.filter((item) =>
    item.title.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <Box
      component="main"
      sx={{ maxWidth: 1200, mx: "auto", px: { xs: 3, md: 5 }, py: 6 }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        gap={3}
        sx={{ mb: 4 }}
      >
        <Box>
          <Typography
            component="h1"
            variant="h2"
            sx={{ fontSize: { xs: 34, md: 42 } }}
          >
            Your library
          </Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            Your saved generations.
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Button
            variant="contained"
            onClick={onCreate}
          >
            New swap
          </Button>
        </Stack>
      </Stack>
      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError("")}>
          {error}
        </Alert>
      )}
      {loading ? (
        <Box sx={{ py: 10, textAlign: "center" }}>
          <CircularProgress aria-label="Loading library" />
        </Box>
      ) : items.length === 0 ? (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "5px",
            py: 10,
            px: 3,
            textAlign: "center",
            bgcolor: "background.paper",
          }}
        >
          <Typography variant="h6">No generations yet</Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>
            Create a swap and your saved video will appear here.
          </Typography>
        </Box>
      ) : (
        <>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 3 }}
          >
            <Typography variant="body2" color="text.secondary">
              {items.length} {items.length === 1 ? "creation" : "creations"}
            </Typography>
            <TextField
              size="small"
              label="Search your library"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </Stack>
          {!filtered.length && (
            <Typography color="text.secondary">
              No creations match your search.
            </Typography>
          )}
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                sm: "repeat(2,1fr)",
                lg: "repeat(3,1fr)",
              },
              gap: 3,
            }}
          >
            {filtered.map((item) => (
              <Box
                key={item.id}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "5px",
                  overflow: "hidden",
                  bgcolor: "background.paper",
                }}
              >
                <Box
                  sx={{
                    aspectRatio: "16/10",
                    bgcolor: "action.selected",
                    position: "relative",
                    display: "grid",
                    placeItems: "center",
                  }}
                >
                  {item.videoUrl ? (
                    <Box
                      component="button"
                      aria-label={`Play ${item.title}`}
                      onClick={() => setSelected(item)}
                      sx={{
                        p: 0,
                        width: "100%",
                        height: "100%",
                        bgcolor: "#18181b",
                        border: 0,
                        cursor: "pointer",
                      }}
                    >
                      <video
                        src={item.videoUrl}
                        preload="metadata"
                        muted
                        playsInline
                        style={{
                          width: "100%",
                          height: "100%",
                          objectFit: "cover",
                          pointerEvents: "none",
                        }}
                      />
                    </Box>
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      {item.status === "completed" ? "Video not saved" : "Video in progress"}
                    </Typography>
                  )}
                  <Chip
                    size="small"
                    label={
                      item.saved ? "Saved" : item.status.replaceAll("_", " ")
                    }
                    sx={{
                      position: "absolute",
                      top: 12,
                      left: 12,
                      bgcolor: "background.paper",
                      textTransform: "capitalize",
                    }}
                  />
                </Box>
                <Box sx={{ p: 2.5 }}>
                  <Typography variant="h6" noWrap>
                    {item.title}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {new Date(item.createdAt * 1000).toLocaleDateString()} ·{" "}
                    {item.duration ? `${item.duration}s · ` : ""}
                    {item.provider}
                  </Typography>
                  {!item.saved && item.status === "completed" && (
                    <Typography
                      variant="caption"
                      display="block"
                      color="text.secondary"
                      sx={{ mt: 1 }}
                    >
                      Video hasn't been saved yet. Return to Review to save it.
                    </Typography>
                  )}
                  <Stack
                    direction="row"
                    justifyContent="space-between"
                    sx={{ mt: 2 }}
                  >
                    {item.videoUrl ? (
                      <Button
                        component="a"
                        href={item.videoUrl}
                        download={`${item.title.replace(/[^a-zA-Z0-9 -]/g, "").slice(0, 80) || "swapmein"}.${item.videoExtension === "webm" ? "webm" : "mp4"}`}
                        size="small"
                        startIcon={<DownloadRounded />}
                      >
                        Download
                      </Button>
                    ) : (
                      <Box />
                    )}
                    <IconButton
                      aria-label={`Delete ${item.title}`}
                      onClick={() => setDeleting(item)}
                    >
                      <DeleteOutlineRounded fontSize="small" />
                    </IconButton>
                  </Stack>
                </Box>
              </Box>
            ))}
          </Box>
        </>
      )}
      <Dialog
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          {selected?.title}
          <IconButton
            aria-label="Close video"
            onClick={() => setSelected(null)}
            sx={{ position: "absolute", right: 12, top: 12 }}
          >
            <CloseRounded />
          </IconButton>
        </DialogTitle>
        <DialogContent>
          {selected?.videoUrl && (
            <video
              src={selected.videoUrl}
              controls
              autoPlay
              style={{ width: "100%", borderRadius: "5px" }}
            />
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(deleting)}
        onClose={() => !busy && setDeleting(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>Delete this creation?</DialogTitle>
        <DialogContent>
          <Typography color="text.secondary" sx={{ mb: 3 }}>
            “{deleting?.title}” and its saved video will be removed from your
            library.
          </Typography>
          <Stack direction="row" justifyContent="flex-end" spacing={1}>
            <Button disabled={busy} onClick={() => setDeleting(null)}>
              Keep it
            </Button>
            <Button
              variant="contained"
              disabled={busy}
              onClick={() => void remove()}
            >
              Delete creation
            </Button>
          </Stack>
        </DialogContent>
      </Dialog>
    </Box>
  );
}
