import type { ClipAsset, WalkthroughState } from "../model/model";
import type { JobSettingsUsed, SelectionTarget } from "./livePipeline";
import { reportClientError } from "./diagnostics";
import { readLocalClip } from "./localMedia";
import { TARGET_COLORS, MAX_SOURCE_BYTES, MAX_REFERENCE_BYTES } from "../limits";

export interface JobRecovery {
  action: "retry" | "save" | "support";
  message: string;
  settings?: JobSettingsUsed;
  trim?: [number, number];
  targets?: SelectionTarget[];
  anchor?: { x: number; y: number; time: number } | null;
  target_name?: string;
  source_url?: string | null;
  reference_urls?: (string | null)[];
  credits_charged?: number;
  credits_returned?: number;
}

export interface RetryDraft extends WalkthroughState {
  retryNotice: string;
  retryNeedsSource: boolean;
}

/** Download remaining inputs now: signed links can expire while someone reviews. */
export async function restoreRetryDraft(recovery: JobRecovery, signal: AbortSignal): Promise<RetryDraft> {
  const urls: string[] = [];
  async function download(url: string | null | undefined, maximum: number): Promise<Blob | null> {
    if (!url) return null;
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.hostname !== "storage.googleapis.com") throw new Error("Unexpected recovery link.");
    const response = await fetch(url, {signal, credentials: "omit", cache: "no-store"});
    if (response.status === 404 || response.status === 410) return null;
    if (!response.ok) throw new Error("The saved files could not be loaded. Refresh the job details and try again.");
    if (Number(response.headers.get("Content-Length")) > maximum) throw new Error("The saved file is too large to restore.");
    const blob = await response.blob();
    if (blob.size > maximum) throw new Error("The saved file is too large to restore.");
    return blob;
  }
  function localUrl(blob: Blob) {
    const local = URL.createObjectURL(blob);
    urls.push(local);
    return local;
  }
  try {
    const source = await download(recovery.source_url, MAX_SOURCE_BYTES);
    let clip: ClipAsset | null = null;
    let unreadableSource = false;
    if (source) {
      // Cloud Storage keeps uploads as generic binary. Restore the container's
      // media type before making a Blob URL, especially for Safari.
      const file = await recoveredVideoFile(source);
      const sourceUrl = localUrl(file);
      try {
        clip = await readLocalClip(file, sourceUrl);
      } catch (cause) {
        signal.throwIfAborted();
        unreadableSource = true;
        URL.revokeObjectURL(sourceUrl);
        urls.splice(urls.indexOf(sourceUrl), 1);
        reportClientError("library_video_decode", cause);
      }
    }
    const references: WalkthroughState["references"] = [];
    const assignments: Record<string, string> = {};
    const targets = recovery.targets?.length ? recovery.targets.map((target, index) => ({
      id: target.id, name: target.name, anchor: {...target.anchor}, color: target.color ?? TARGET_COLORS[index % TARGET_COLORS.length],
    })) : recovery.settings?.replace_all_people ? [{id: "all-people", name: "All people", anchor: {x: 0.5, y: 0.5, time: recovery.trim?.[0] ?? 0}, color: TARGET_COLORS[0]}] : recovery.anchor ? [{id: "selected", name: recovery.target_name ?? "Selected person", anchor: recovery.anchor, color: TARGET_COLORS[0]}] : [];
    for (const [index, remote] of (recovery.reference_urls ?? []).entries()) {
      const photo = await download(remote, MAX_REFERENCE_BYTES);
      if (!photo) continue;
      const url = localUrl(photo);
      const id = `retry-reference-${index}`;
      references.push({id, name: `Reference ${index + 1}.jpg`, url});
      for (const target of targets) {
        const targetIndex = recovery.targets?.find((item) => item.id === target.id)?.reference_index ?? 0;
        if (targetIndex === index) assignments[target.id] = id;
      }
    }
    signal.throwIfAborted();
    const settings = recovery.settings ?? {};
    return {clip, trim: recovery.trim ?? [0, clip ? Math.min(15, Math.floor(clip.duration)) : 0], targets, references, assignments,
      issues: [], decisions: {}, easyMode: settings.easy_mode ?? true, faceMesh: settings.face_mesh, submitWithoutAudio: settings.submit_without_audio ?? false, addWatermark: settings.add_watermark ?? false, enhancedCharacterMasking: settings.enhanced_masking ?? true,
      replaceAllPeople: settings.replace_all_people ?? false, retryNeedsSource: !clip,
      retryNotice: unreadableSource ? "This browser could not open the saved video. Upload the original clip; your trim, character selections, and available photos are restored."
        : !clip ? "Upload the original clip to retry. Saved settings and available references are restored; review them before submitting."
        : "Your clip and available references are restored. Review the trim, people, references, and current credit cost before submitting again."};
  } catch (error) {
    for (const url of urls) URL.revokeObjectURL(url);
    throw error;
  }
}


/** Keep the original bytes; only recover the container type lost on upload. */
export async function recoveredVideoFile(blob: Blob): Promise<File> {
  const prefix = blob.slice(0, 16);
  const bytes = typeof prefix.arrayBuffer === "function" ? await prefix.arrayBuffer() : await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(new Error("The saved video header could not be read."));
    reader.readAsArrayBuffer(prefix);
  });
  const header = new Uint8Array(bytes);
  const text = (start: number, end: number) => String.fromCharCode(...header.slice(start, end));
  let type = blob.type.startsWith("video/") ? blob.type : "application/octet-stream";
  let extension = type === "video/webm" ? "webm" : type === "video/quicktime" ? "mov" : "mp4";
  if (text(4, 8) === "ftyp") {
    const quicktime = text(8, 12) === "qt  ";
    type = quicktime ? "video/quicktime" : "video/mp4";
    extension = quicktime ? "mov" : "mp4";
  } else if (header[0] === 0x1a && header[1] === 0x45 && header[2] === 0xdf && header[3] === 0xa3) {
    type = "video/webm"; extension = "webm";
  }
  return new File([blob], `Recovered clip.${extension}`, {type});
}
