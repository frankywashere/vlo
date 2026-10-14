export interface PreparationProgressState {
  phase: "checking_access" | "checking_capacity" | "uploading" | "confirming" | "queued" | "preparing";
  message: string;
  uploadPercent?: number;
}
