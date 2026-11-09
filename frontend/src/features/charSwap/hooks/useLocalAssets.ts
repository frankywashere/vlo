import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCharSwapStore } from "../useCharSwapStore";
import { readLocalClip, validateReference } from "../services/localMedia";
import { createLocalId } from "../utils/createLocalId";

export function useLocalAssets() {
  const urls = useRef(new Set<string>());
  const clipRequest = useRef(0);
  const session = useRef(0);
  const referenceRequests = useRef(new Map<string, number>());
  const pending = useRef(new Set<string>());
  const referenceImports = useRef(new Map<string, string>());
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
      ...references.flatMap((reference) => [reference.url, reference.cleanup?.originalUrl, reference.cleanup?.cleanedUrl]),
    ]);
    // Retry drafts also own local copies downloaded from their saved inputs.
    for (const url of retained) if (url?.startsWith("blob:")) urls.current.add(url);
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
    referenceImports.current.clear();
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
        referenceImports.current.clear();
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
  async function importReferenceFiles(targetId: string, files: File[], append: boolean) {
    if (!files.length) return;
    const request = (referenceRequests.current.get(targetId) ?? 0) + 1;
    if (!append) referenceRequests.current.set(targetId, request);
    if(!append)for(const [id,target]of referenceImports.current)if(target===targetId&&id.startsWith("replace:"))referenceImports.current.delete(id);
    const importId = `${append?"add":"replace"}:${createLocalId()}`;
    referenceImports.current.set(importId, targetId);
    const currentSession = session.current;
    const currentClipId = useCharSwapStore.getState().clip?.id;
    const selectionVersion = useCharSwapStore.getState().selectionVersion;
    const originalAnchor = useCharSwapStore.getState().targets.find(person => person.id === targetId)?.anchor;
    const created: string[] = [];
    setPendingReferences([...new Set(referenceImports.current.values())]);
    const stillCurrent = () => currentSession === session.current
      && currentClipId === useCharSwapStore.getState().clip?.id
      && selectionVersion === useCharSwapStore.getState().selectionVersion
      && (append || request === referenceRequests.current.get(targetId))
      && useCharSwapStore.getState().targets.some(person => person.id === targetId && person.anchor === originalAnchor);
    try {
      const assets = [];
      for (const file of files) {
        const url = URL.createObjectURL(file);
        urls.current.add(url); pending.current.add(url); created.push(url);
        await validateReference(file, url);
        assets.push({id: createLocalId(), name: file.name, url});
      }
      if (!stillCurrent()) {for (const url of created) release(url); return;}
      if (append) {
        if (!useCharSwapStore.getState().addReferences(targetId, assets)) for (const url of created) release(url);
      } else useCharSwapStore.getState().assignReference(targetId, assets[0]);
    } catch (error) {
      for (const url of created) release(url);
      if (stillCurrent()) useCharSwapStore.getState().setError(error instanceof Error ? error.message : "Couldn't open that reference.");
    } finally {
      for (const url of created) pending.current.delete(url);
      referenceImports.current.delete(importId);
      if (currentSession === session.current) setPendingReferences([...new Set(referenceImports.current.values())]);
    }
  }
  function importReference(targetId: string, file: File) {return importReferenceFiles(targetId, [file], false);}
  function importAdditionalReferences(targetId: string, files: File[]) {return importReferenceFiles(targetId, files, true);}
  return {
    busy,
    pendingReferences,
    cancelPending,
    importClip,
    importReference,
    importAdditionalReferences,
  };
}
