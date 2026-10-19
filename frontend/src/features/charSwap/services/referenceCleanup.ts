import {UnsupportedCleanupError} from "./browserCleanupProtocol";
import type {CleanupProgress, CleanupReply, CleanupRequest} from "./browserCleanupProtocol";

// Retained for older gateway configuration; photos are no longer sent there.
export function isReferenceCleanupEndpoint(value: unknown): value is string {
  return value === "/api/references/clean" || value === "https://recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean";
}
let queue: Promise<void> = Promise.resolve();
let worker: Worker | undefined;
let ready: Promise<void> | undefined;
let unsupported = false;
let sequence = 0;
const waiting = new Set<() => void>();
let idle: ReturnType<typeof setTimeout> | undefined;
let progress: ((value: CleanupProgress) => void) | undefined;
let lastProgress: CleanupProgress = {phase:"loading"};
const abortError = () => new DOMException("Reference cleanup was canceled.","AbortError");
function stop() {
  clearTimeout(idle); worker?.terminate(); worker = undefined; ready = undefined;
  for (const cancel of [...waiting]) cancel();
}
function releaseLater() {clearTimeout(idle); idle = setTimeout(stop, 60_000);}
function supported() {
  if (unsupported || !globalThis.isSecureContext || typeof Worker === "undefined" ||
    !navigator.gpu || typeof OffscreenCanvas === "undefined" || typeof createImageBitmap !== "function")
    throw new UnsupportedCleanupError();
}
function waitFor<T>(target: Worker, match: (reply: CleanupReply) => T | undefined, timeout: number): Promise<T> {
  return new Promise((resolve,reject) => {
    const timer = setTimeout(() => fail(new Error("Background cleanup timed out. You can use Original.")),timeout);
    const cancel = () => {finish(); reject(abortError());};
    waiting.add(cancel);
    function finish() {waiting.delete(cancel); clearTimeout(timer); target.removeEventListener("message",message); target.removeEventListener("error",crash);}
    function fail(error: Error) {finish(); stop(); reject(error);}
    function crash() {unsupported = true; fail(new UnsupportedCleanupError());}
    function message(event: MessageEvent<CleanupReply>) {
      const reply = event.data;
      if (reply.type === "progress") {lastProgress = reply.progress; progress?.(reply.progress); return;}
      if (reply.type === "error") {
        if (reply.kind === "unsupported") unsupported = true;
        fail(reply.kind === "unsupported" ? new UnsupportedCleanupError() : new Error(reply.message)); return;
      }
      const value = match(reply);
      if (value !== undefined) {finish(); resolve(value);}
    }
    target.addEventListener("message",message); target.addEventListener("error",crash);
  });
}
function initialize() {
  supported(); clearTimeout(idle);
  if (!ready) {
    lastProgress = {phase:"loading"};
    try {worker = new Worker(new URL("../workers/referenceCleanup.worker.ts",import.meta.url),{type:"module"});}
    catch {unsupported = true; throw new UnsupportedCleanupError();}
    ready = waitFor(worker, reply => reply.type === "ready" ? true : undefined,90_000).then(() => undefined);
    worker.postMessage({type:"initialize"} satisfies CleanupRequest);
  }
  return ready;
}
// Fetch and compile once while the user trims their clip, without uploading a photo.
export function warmReferenceCleanup(): void {
  try {void initialize().then(releaseLater, () => undefined);} catch { /* Unsupported devices keep Original. */ }
}
export function cleanReferenceBackground(url: string, signal: AbortSignal, _legacyEndpoint?: string,
  onProgress?: (value: CleanupProgress) => void): Promise<Blob> {
  if (signal.aborted) return Promise.reject(abortError());
  const task = queue.then(async () => {
    if (signal.aborted) throw abortError();
    supported(); clearTimeout(idle);
    let cancel!: () => void;
    const canceled = new Promise<never>((_,reject) => {cancel = () => {stop(); reject(abortError());};});
    signal.addEventListener("abort",cancel,{once:true});
    progress = onProgress;
    try {
      const run = async () => {
        const loading = initialize(); onProgress?.(lastProgress); await loading;
        if (signal.aborted) throw abortError();
        // Only local draft blobs are read. No authenticated POST or cloud fallback.
        if (!url.startsWith("blob:")) throw new Error("Choose a local reference image to clean.");
        const response = await fetch(url,{signal});
        if (!response.ok) throw new Error("The original reference could not be read.");
        const image = await response.blob();
        if (!image.size || image.size > 5*1024*1024) throw new Error("Choose a reference smaller than 5 MiB.");
        if (signal.aborted || !worker) throw abortError();
        const id = ++sequence;
        const result = waitFor(worker, reply => reply.type === "result" && reply.id === id ? reply.image : undefined,30_000);
        worker.postMessage({type:"clean",id,image} satisfies CleanupRequest);
        const cleaned = await result;
        if (!(cleaned instanceof Blob) || cleaned.type !== "image/jpeg" || !cleaned.size || cleaned.size > 5*1024*1024)
          throw new Error("The cleaned reference could not be read. Use Original.");
        return cleaned;
      };
      return await Promise.race([run(),canceled]);
    } finally {signal.removeEventListener("abort",cancel); progress = undefined; releaseLater();}
  });
  queue = task.then(() => undefined, () => undefined);
  // Cancel a queued photo immediately, without interrupting the active photo.
  return new Promise((resolve,reject) => {
    const cancel = () => reject(abortError());
    signal.addEventListener("abort",cancel,{once:true});
    task.then(resolve,reject).finally(() => signal.removeEventListener("abort",cancel));
  });
}
