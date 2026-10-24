import { MAX_REFERENCE_BYTES, MAX_SOURCE_BYTES, MAX_TARGET_PEOPLE } from "../limits";
import { referenceCleanupBlocker } from "../model/model";
import type { WalkthroughState } from "../model/model";
import { trimDuration } from "../utils/trimRange";
import type { LongRenderSetup } from "./api";

/** Keep the normal walkthrough's selected cleanup mode authoritative at upload. */
export function previewSetup(state: WalkthroughState, maximumSeconds: number) {
  const clip = state.clip;
  if (!clip) throw new Error("Upload a source clip first.");
  const [start, end] = state.trim;
  const duration = trimDuration(state.trim);
  if (![start, end, clip.duration].every(Number.isFinite) || start < 0 || end > clip.duration + 1e-6 ||
    duration < 4 || duration > maximumSeconds || Math.abs(duration - Math.round(duration)) > 1e-6)
    throw new Error(`Choose a whole-number duration between 4 and ${maximumSeconds} seconds.`);
  if (!state.targets.length || state.targets.length > MAX_TARGET_PEOPLE ||
    new Set(state.targets.map(target => target.id)).size !== state.targets.length ||
    (state.replaceAllPeople && state.targets.length !== 1))
    throw new Error("Choose up to five people, or replace everyone with one reference.");
  const references: { id: string; name: string; url: string }[] = [];
  const targets = state.targets.map(target => {
    const { x, y, time } = target.anchor;
    if (!/^[A-Za-z0-9_-]{1,40}$/.test(target.id) || !target.name.trim() || target.name.length > 40 ||
      (!state.replaceAllPeople && (![x, y, time].every(Number.isFinite) || x < 0 || x > 1 || y < 0 || y > 1 || time < start || time >= end)))
      throw new Error("Select each person again inside the chosen section.");
    const assigned = state.references.find(reference => reference.id === state.assignments[target.id]);
    if (!assigned?.url) throw new Error(`Assign a reference image to ${target.name}.`);
    const blocker = referenceCleanupBlocker(assigned);
    if (blocker) throw new Error(`${target.name}: ${blocker}`);
    const url = assigned.cleanup?.mode === "clean" ? assigned.cleanup.cleanedUrl! : assigned.cleanup?.originalUrl ?? assigned.url;
    let referenceIndex = references.findIndex(reference => reference.id === assigned.id || reference.url === url);
    if (referenceIndex < 0) {
      referenceIndex = references.length;
      references.push({ id: assigned.id, name: assigned.name, url });
    }
    return { id: target.id, name: target.name, anchor: { ...target.anchor }, reference_index: referenceIndex };
  });
  const setup: LongRenderSetup = {
    trim: [start, start + Math.round(duration)], preparation_version: 2, auto_generate: false,
    easy_mode: state.easyMode ?? true, enhanced_character_masking: state.enhancedCharacterMasking ?? true,
    submit_without_audio: state.submitWithoutAudio ?? false, add_watermark: state.addWatermark ?? false,
    replace_all_people: state.replaceAllPeople ?? false,
    anchor: state.replaceAllPeople ? null : targets[0].anchor,
    ...(!state.replaceAllPeople ? { targets } : {}),
    ...(typeof state.faceMesh === "boolean" ? { face_mesh: state.faceMesh } : {}),
  };
  return { setup, clip, references };
}

/** Only device blobs and same-origin fixtures enter the upload pipeline. */
export async function previewAssetFile(url: string, name: string, kind: "source" | "reference", signal: AbortSignal): Promise<File> {
  const parsed = new URL(url, globalThis.location.origin);
  if (!["blob:", "https:", "http:"].includes(parsed.protocol) ||
    (parsed.protocol !== "blob:" && parsed.origin !== globalThis.location.origin))
    throw new Error("Upload this media from your device before preparing it.");
  const maximumBytes = kind === "source" ? MAX_SOURCE_BYTES : MAX_REFERENCE_BYTES;
  signal.throwIfAborted();
  const response = await fetch(url, { signal, credentials: "omit", cache: "no-store" });
  if (!response.ok) throw new Error(`The ${kind} could not be opened. Upload it again.`);
  if (Number(response.headers.get("Content-Length")) > maximumBytes)
    throw new Error(`Choose a ${kind} under ${kind === "source" ? 100 : 5} MiB.`);
  const blob = await response.blob();
  signal.throwIfAborted();
  if (!blob.size || blob.size > maximumBytes) throw new Error(`Choose a ${kind} under ${kind === "source" ? 100 : 5} MiB.`);
  if (kind === "reference" && !/^image\/(jpeg|png|webp)$/.test(blob.type))
    throw new Error("Choose a JPG, PNG, or WebP reference image.");
  return new File([blob], name, { type: blob.type || "video/mp4" });
}
