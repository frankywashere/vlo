export const CLEANUP_FAILURE_REASONS = ["previous_interruption", "insecure_context", "worker_api_missing", "webgpu_api_missing", "canvas_api_missing", "bitmap_api_missing", "worker_start_failed", "worker_crash", "worker_error", "model_load_timeout", "inference_timeout", "gpu_adapter_request", "gpu_adapter_missing", "gpu_f16_missing", "model_download", "model_integrity", "gpu_session_create", "cpu_runtime_load", "cpu_model_compile", "image_decode", "canvas_context", "gpu_inference", "cpu_inference", "model_output_invalid", "image_encode", "model_not_ready", "runtime_unavailable"] as const;
export type CleanupFailureReason = typeof CLEANUP_FAILURE_REASONS[number];
export const UNSUPPORTED_CLEANUP = "Background cleanup isn’t supported on your hardware. You can use the original image.";
export const MODEL_PATH = "/models/birefnet-lite-512/962e67969ec6acec9c19fab76acee4f5e22e990c/model_fp16.onnx";
export const RUNTIME_PATH = "/ml/ort-1.31.0-8d85527a0/";
export const CPU_RUNTIME_PATH = "/ml/litert-2.5.3-cpu-v5-diagnostics/";
export const CPU_MODEL_CACHE = "recastfx-cpu-reference-model-v1";
export const CPU_MODEL_PATH = "/models/ormbg/8dfdacc4ebceafd4d55195fdbd3baf8470610518/ormbg.tflite";
export interface CleanupProgress {phase: "queued" | "loading" | "cleaning"; percent?: number; downloadMB?: number; startedAt?: number; expectedMs?: number}
export type CleanupRequest = {type: "initialize"} | {type: "clean"; id: number; image: Blob};
export type CleanupReply =
  | {type: "ready"}
  | {type: "progress"; progress: CleanupProgress}
  | {type: "result"; id: number; image: Blob}
  | {type: "error"; id?: number; kind: "unsupported" | "download" | "image" | "runtime"; reason?: CleanupFailureReason; message: string};
export class UnsupportedCleanupError extends Error {
  constructor() {super(UNSUPPORTED_CLEANUP); this.name = "UnsupportedCleanupError";}
}

export class CleanupSkippedError extends Error {
  constructor() {super("Cleanup skipped for Original.");this.name="CleanupSkippedError";}
}

export class InterruptedCleanupError extends Error {
  constructor() {super("Cleanup stopped repeatedly on this device. Use Original to continue.");this.name="InterruptedCleanupError";}
}
