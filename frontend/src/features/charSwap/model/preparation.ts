export interface PreparationProgressState {
  phase: "checking_access" | "checking_capacity" | "uploading" | "confirming" | "queued" | "preparing";
  message: string;
  uploadPercent?: number;
}

export function isUploadUnconfirmed(progress: PreparationProgressState | null): boolean {
  return progress !== null && progress.phase !== "queued" && progress.phase !== "preparing";
}
