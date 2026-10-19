export const UNSUPPORTED_CLEANUP = "Background cleanup isn’t supported on your hardware. You can use the original image.";
export const MODEL_PATH = "/models/birefnet-lite-512/962e67969ec6acec9c19fab76acee4f5e22e990c/model_fp16.onnx";
export const RUNTIME_PATH = "/ml/ort-1.31.0-8d85527a0/";
export interface CleanupProgress {phase: "loading" | "cleaning"; percent?: number}
export type CleanupRequest = {type: "initialize"} | {type: "clean"; id: number; image: Blob};
export type CleanupReply =
  | {type: "ready"}
  | {type: "progress"; progress: CleanupProgress}
  | {type: "result"; id: number; image: Blob}
  | {type: "error"; id?: number; kind: "unsupported" | "download" | "image"; message: string};
export class UnsupportedCleanupError extends Error {
  constructor() {super(UNSUPPORTED_CLEANUP); this.name = "UnsupportedCleanupError";}
}
