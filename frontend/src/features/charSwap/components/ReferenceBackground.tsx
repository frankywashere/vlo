import { useEffect, useRef } from "react";
import { Alert, CircularProgress, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import type { ReferenceAsset } from "../model/model";
import { useCharSwapStore } from "../useCharSwapStore";
import { cleanReferenceBackground } from "../services/referenceCleanup";
import { createLocalId } from "../utils/createLocalId";

interface ReferenceBackgroundProps {
  targetId: string;
  reference: ReferenceAsset;
}

export function ReferenceBackground({targetId, reference}: ReferenceBackgroundProps) {
  const operation = useRef<AbortController | null>(null);
  const original = reference.cleanup?.originalUrl ?? reference.url;
  function current() {
    const state = useCharSwapStore.getState();
    return state.references.find(item => item.id === state.assignments[targetId]);
  }
  useEffect(() => () => {
    operation.current?.abort();
    const asset = current();
    if (asset?.cleanup?.pending && asset.cleanup.originalUrl === original)
      useCharSwapStore.getState().assignReference(targetId, {...asset, url: original,
        cleanup: {...asset.cleanup, mode: "original", pending: false}});
  }, [targetId, original]); // Changing the source or leaving the draft cancels stale work.

  async function prepare() {
    if (operation.current && !operation.current.signal.aborted) return;
    const controller = new AbortController();
    operation.current = controller;
    const id = createLocalId(); // A shared reference can be cleaned independently per person.
    useCharSwapStore.getState().assignReference(targetId, {...reference, id,
      cleanup: {originalUrl: original, mode: "clean", pending: true}});
    try {
      const image = await cleanReferenceBackground(original, controller.signal);
      const asset = current();
      if (controller.signal.aborted || asset?.id !== id) return;
      const cleanedUrl = URL.createObjectURL(image);
      const mode = asset.cleanup?.mode ?? "original";
      useCharSwapStore.getState().assignReference(targetId, {...asset,
        url: mode === "clean" ? cleanedUrl : original,
        cleanup: {originalUrl: original, cleanedUrl, mode}});
    } catch (error) {
      const asset = current();
      if (asset?.id === id) useCharSwapStore.getState().assignReference(targetId, {...asset, url: original,
        cleanup: {originalUrl: original, mode: "original", error: controller.signal.aborted
          ? "Cleanup stopped. The original image is still available." : error instanceof Error ? error.message : "Cleanup did not finish. Use the original."}});
    } finally {
      if (operation.current === controller) operation.current = null;
    }
  }
  // Prepare automatically once per uploaded source; the original remains usable
  // while it runs, and the user can choose it without waiting for cleanup.
  useEffect(() => {if (!reference.cleanup) void prepare();}, [original]);
  function choose(mode: "original" | "clean") {
    if (mode === "clean" && !reference.cleanup?.cleanedUrl && !reference.cleanup?.pending) {void prepare(); return;}
    useCharSwapStore.getState().assignReference(targetId, {...reference,
      // Detach completed shared references, while preserving an active request's ID.
      id: reference.cleanup?.pending ? reference.id : createLocalId(),
      url: mode === "clean" ? reference.cleanup?.cleanedUrl ?? original : original,
      cleanup: {...reference.cleanup, originalUrl: original, mode}});
  }
  return <Stack spacing={1}>
    <ToggleButtonGroup exclusive size="small" value={reference.cleanup?.mode ?? "original"}
      aria-label="Reference background" onChange={(_, value: "original" | "clean" | null) => {if (value) choose(value);}}>
      <ToggleButton value="original">Original</ToggleButton>
      <ToggleButton value="clean">Clean background</ToggleButton>
    </ToggleButtonGroup>
    {reference.cleanup?.pending && <Stack direction="row" spacing={1} alignItems="center" role="status">
      <CircularProgress size={14} /><Typography variant="caption">Cleaning background… You can use Original while this finishes.</Typography>
    </Stack>}
    {reference.cleanup?.error && <Alert severity="info">{reference.cleanup.error}</Alert>}
  </Stack>;
}
