import type { ClipAsset, WalkthroughState } from "../model/model";
import type { JobSettingsUsed, SelectionTarget } from "./livePipeline";
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
  async function download(url: string | null | undefined, maximum: number): Promise<string | null> {
    if (!url) return null;
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.hostname !== "storage.googleapis.com") throw new Error("Unexpected recovery link.");
    const response = await fetch(url, {signal, credentials: "omit", cache: "no-store"});
    if (response.status === 404 || response.status === 410) return null;
    if (!response.ok) throw new Error("The saved files could not be loaded. Refresh the job details and try again.");
    if (Number(response.headers.get("Content-Length")) > maximum) throw new Error("The saved file is too large to restore.");
    const blob = await response.blob();
    if (blob.size > maximum) throw new Error("The saved file is too large to restore.");
    const local = URL.createObjectURL(blob);
    urls.push(local);
    return local;
  }
  try {
    const source = await download(recovery.source_url, MAX_SOURCE_BYTES);
    let clip: ClipAsset | null = null;
    if (source) {
      // Metadata comes from the original upload, not the trimmed preparation copy.
      clip = await readLocalClip(new File([], "Recovered clip.mp4", {type: "video/mp4"}), source);
    }
    const references: WalkthroughState["references"] = [];
    const assignments: Record<string, string> = {};
    const targets = recovery.targets?.length ? recovery.targets.map((target, index) => ({
      id: target.id, name: target.name, anchor: {...target.anchor}, color: target.color ?? TARGET_COLORS[index % TARGET_COLORS.length],
    })) : recovery.settings?.replace_all_people ? [{id: "all-people", name: "All people", anchor: {x: 0.5, y: 0.5, time: recovery.trim?.[0] ?? 0}, color: TARGET_COLORS[0]}] : recovery.anchor ? [{id: "selected", name: recovery.target_name ?? "Selected person", anchor: recovery.anchor, color: TARGET_COLORS[0]}] : [];
    for (const [index, remote] of (recovery.reference_urls ?? []).entries()) {
      const url = await download(remote, MAX_REFERENCE_BYTES);
      if (!url) continue;
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
      issues: [], decisions: {}, easyMode: settings.easy_mode ?? true, submitWithoutAudio: settings.submit_without_audio ?? false, enhancedCharacterMasking: settings.enhanced_masking ?? true,
      replaceAllPeople: settings.replace_all_people ?? false, retryNeedsSource: !clip,
      retryNotice: !clip ? "Upload the original clip to retry. Saved settings and available references are restored; review them before submitting."
        : "Your clip and available references are restored. Review the trim, people, references, and current credit cost before submitting again."};
  } catch (error) {
    for (const url of urls) URL.revokeObjectURL(url);
    throw error;
  }
}
