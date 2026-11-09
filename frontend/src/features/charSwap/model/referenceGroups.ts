import type { ReferenceAsset, WalkthroughState } from "./model";

export const MAX_REFERENCE_IMAGES = 6;
export type ReferenceDraft = Pick<WalkthroughState, "targets" | "assignments" | "references" | "referenceGroups">;

/** The legacy assignment remains the authoritative primary photo. */
export function referenceIds(state: Pick<ReferenceDraft, "assignments" | "referenceGroups">, targetId: string): string[] {
  const primary = state.assignments[targetId];
  return [...new Set([...(primary ? [primary] : []), ...(state.referenceGroups?.[targetId] ?? [])])];
}

export function targetReferences(state: ReferenceDraft, targetId: string): ReferenceAsset[] {
  return referenceIds(state, targetId).flatMap(id => {
    const asset = state.references.find(reference => reference.id === id);
    return asset ? [asset] : [];
  });
}

export function selectedReferenceUrl(reference: ReferenceAsset): string {
  return reference.cleanup?.mode === "clean"
    ? reference.cleanup.cleanedUrl ?? reference.url
    : reference.cleanup?.originalUrl ?? reference.url;
}

/** Use this exact list for both the customer quote and upload indexing. */
export function submittedReferences(state: ReferenceDraft): ReferenceAsset[] {
  const result: ReferenceAsset[] = [];
  for (const target of state.targets) for (const reference of targetReferences(state, target.id)) {
    const url = selectedReferenceUrl(reference);
    if (!result.some(item => item.id === reference.id || selectedReferenceUrl(item) === url)) result.push(reference);
  }
  return result;
}

export function retainAssignedReferences<T extends ReferenceDraft>(state: T): T {
  const ids = new Set(state.targets.flatMap(target => referenceIds(state, target.id)));
  return {...state, references: state.references.filter(reference => ids.has(reference.id))};
}
