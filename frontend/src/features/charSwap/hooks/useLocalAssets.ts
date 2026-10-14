import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCharSwapStore } from "../useCharSwapStore";
import { readLocalClip, validateReference } from "../services/localMedia";

export function useLocalAssets() {
  const urls = useRef(new Set<string>());
  const clipRequest = useRef(0);
  const session = useRef(0);
  const referenceRequests = useRef(new Map<string, number>());
  const pending = useRef(new Set<string>());
  const [busy, setBusy] = useState(false);
  const [pendingReferences, setPendingReferences] = useState<string[]>([]);
  const { clip, references } = useCharSwapStore(
    useShallow((state) => ({ clip: state.clip, references: state.references })),
  );
  useEffect(
    () => () => {
      clipRequest.current += 1;
      session.current += 1;
      for (const url of urls.current) URL.revokeObjectURL(url);
      urls.current.clear();
    },
    [],
  );
  useEffect(() => {
    const retained = new Set([
      clip?.url,
      ...references.map((reference) => reference.url),
    ]);
    for (const url of urls.current)
      if (!retained.has(url) && !pending.current.has(url)) release(url);
  }, [clip, references]);

  function release(url: string) {
    URL.revokeObjectURL(url);
    urls.current.delete(url);
  }
  function cancelPending() {
    clipRequest.current += 1;
    session.current += 1;
    referenceRequests.current.clear();
    for (const url of pending.current) release(url);
    pending.current.clear();
    setBusy(false);
    setPendingReferences([]);
  }
  async function importClip(file: File) {
    const request = ++clipRequest.current;
    const url = URL.createObjectURL(file);
    urls.current.add(url);
    pending.current.add(url);
    setBusy(true);
    useCharSwapStore.getState().setError(null);
    try {
      const clip = await readLocalClip(file, url);
      if (request === clipRequest.current) {
        session.current += 1;
        referenceRequests.current.clear();
        setPendingReferences([]);
        useCharSwapStore.getState().loadClip(clip);
      } else release(url);
    } catch (error) {
      release(url);
      if (request === clipRequest.current)
        useCharSwapStore
          .getState()
          .setError(
            error instanceof Error
              ? error.message
              : "Couldn't open that video.",
          );
    } finally {
      pending.current.delete(url);
      if (request === clipRequest.current) setBusy(false);
    }
  }
  async function importReference(targetId: string, file: File) {
    const request = (referenceRequests.current.get(targetId) ?? 0) + 1;
    referenceRequests.current.set(targetId, request);
    const currentSession = session.current;
    const currentClipId = useCharSwapStore.getState().clip?.id;
    const selectionVersion = useCharSwapStore.getState().selectionVersion;
    const originalAnchor = useCharSwapStore
      .getState()
      .targets.find((person) => person.id === targetId)?.anchor;
    const url = URL.createObjectURL(file);
    urls.current.add(url);
    pending.current.add(url);
    setPendingReferences((ids) => [...new Set([...ids, targetId])]);
    const stillCurrent = () =>
      currentSession === session.current &&
      currentClipId === useCharSwapStore.getState().clip?.id &&
      selectionVersion === useCharSwapStore.getState().selectionVersion &&
      request === referenceRequests.current.get(targetId);
    try {
      await validateReference(file, url);
      if (
        stillCurrent() &&
        useCharSwapStore
          .getState()
          .targets.some(
            (person) =>
              person.id === targetId && person.anchor === originalAnchor,
          )
      ) {
        useCharSwapStore.getState().assignReference(targetId, {
          id: crypto.randomUUID(),
          name: file.name,
          url,
        });
      } else release(url);
    } catch (error) {
      release(url);
      if (stillCurrent())
        useCharSwapStore
          .getState()
          .setError(
            error instanceof Error
              ? error.message
              : "Couldn't open that reference.",
          );
    } finally {
      pending.current.delete(url);
      if (
        currentSession === session.current &&
        request === referenceRequests.current.get(targetId)
      )
        setPendingReferences((ids) => ids.filter((id) => id !== targetId));
    }
  }
  return {
    busy,
    pendingReferences,
    cancelPending,
    importClip,
    importReference,
  };
}
