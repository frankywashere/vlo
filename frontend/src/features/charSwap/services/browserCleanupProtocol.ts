export const UNSUPPORTED_CLEANUP = "Background cleanup isn’t supported on your hardware. You can use the original image.";
export const MODEL_PATH = "/models/birefnet-lite-512/962e67969ec6acec9c19fab76acee4f5e22e990c/model_fp16.onnx";
export const RUNTIME_PATH = "/ml/ort-1.31.0-8d85527a0/";
export const CPU_RUNTIME_PATH = "/ml/litert-2.5.3-cpu-v4-white/";
export const CPU_MODEL_CACHE = "recastfx-cpu-reference-model-v1";
export const CPU_MODEL_PATH = "/models/ormbg/8dfdacc4ebceafd4d55195fdbd3baf8470610518/ormbg.tflite";
export interface CleanupProgress {phase: "queued" | "loading" | "cleaning"; percent?: number; downloadMB?: number; startedAt?: number; expectedMs?: number}
export type CleanupRequest = {type: "initialize"} | {type: "clean"; id: number; image: Blob};
export type CleanupReply =
  | {type: "ready"}
  | {type: "progress"; progress: CleanupProgress}
  | {type: "result"; id: number; image: Blob}
  | {type: "error"; id?: number; kind: "unsupported" | "download" | "image"; message: string};
export class UnsupportedCleanupError extends Error {
  constructor() {super(UNSUPPORTED_CLEANUP); this.name = "UnsupportedCleanupError";}
}

export class CleanupSkippedError extends Error {
  constructor() {super("Cleanup skipped for Original.");this.name="CleanupSkippedError";}
}

export class InterruptedCleanupError extends Error {
  constructor() {super("Cleanup stopped repeatedly on this device. Use Original to continue.");this.name="InterruptedCleanupError";}
}
