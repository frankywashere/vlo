import { reportClientError, reportClientTiming, timeClientStage } from "./diagnostics";
import {isIOSCleanupDevice} from "../utils/browserCleanupSupport";
import {CPU_MODEL_CACHE, CPU_MODEL_PATH, CPU_RUNTIME_PATH, CleanupSkippedError, InterruptedCleanupError, UnsupportedCleanupError} from "./browserCleanupProtocol";
import type {CleanupFailureReason, CleanupProgress, CleanupReply, CleanupRequest} from "./browserCleanupProtocol";

// Retained for older gateway configuration; photos are no longer sent there.
export function isReferenceCleanupEndpoint(value: unknown): value is string {
  return value === "/api/references/clean" || value === "https://recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean";
}
let queue: Promise<void> = Promise.resolve();
let worker: Worker | undefined;
let ready: Promise<void> | undefined;
// A previous GPU interruption must not block the new CPU implementation.
let desktopCpuFallback = false;
const cpuCleanup = () => desktopCpuFallback || isIOSCleanupDevice(navigator.userAgent,navigator.platform,navigator.maxTouchPoints);
const interruptedKey = () => cpuCleanup() ? "recastfx-cleanup-cpu-interrupted-v1" : "recastfx-cleanup-interrupted-v1";
function interrupted() {try {return sessionStorage.getItem(interruptedKey()) === "1";} catch {return false;}}
function markRunning(active: boolean) {
  try {if (active) sessionStorage.setItem(interruptedKey(),"1"); else sessionStorage.removeItem(interruptedKey());} catch { /* Storage is optional. */ }
}
const recoveryKey = () => cpuCleanup() ? "recastfx-cleanup-cpu-recovery-v1" : "recastfx-cleanup-gpu-recovery-v1";
function recoveryAttempted() {try {return sessionStorage.getItem(recoveryKey()) === "1";} catch {return false;}}
function clearRecovery() {try {sessionStorage.removeItem(recoveryKey());} catch { /* Storage is optional. */ }}
let previousInterruption = interrupted();
let repeatedInterruption = previousInterruption && recoveryAttempted();
// Interrupted work is not evidence that the hardware lacks cleanup support.
let unsupported = false;
let unsupportedReason: CleanupFailureReason = "runtime_unavailable";
let lastFailureReason: CleanupFailureReason | undefined;
let running = false;
// Normal navigation is not evidence of a WebKit crash. Preserve the in-memory
// worker on bfcache restores, but do not leave a crash marker after a clean exit.
globalThis.addEventListener("pagehide", () => {markRunning(false); clearRecovery();});
globalThis.addEventListener("pageshow", () => {if (running) markRunning(true);});
let sequence = 0;
let generation = 0;
let prefetch: Promise<void> | undefined;
const waiting = new Set<() => void>();
let idle: ReturnType<typeof setTimeout> | undefined;
let progress: ((value: CleanupProgress) => void) | undefined;
let lastProgress: CleanupProgress = {phase:"loading"};
const abortError = () => new DOMException("Reference cleanup was canceled.","AbortError");
function stop() {
  generation++; running = false; markRunning(false); clearRecovery(); clearTimeout(idle); worker?.terminate(); worker = undefined; ready = undefined;
  for (const cancel of [...waiting]) cancel();
}
function releaseLater() {clearTimeout(idle); idle = setTimeout(stop, 60_000);}
const GPU_FALLBACK_REASONS = new Set<CleanupFailureReason>(["webgpu_api_missing", "gpu_adapter_request",
  "gpu_adapter_missing", "gpu_f16_missing", "gpu_session_create", "gpu_inference"]);
function fallbackToCpu(reason: CleanupFailureReason | undefined): boolean {
  if (cpuCleanup() || !reason || !GPU_FALLBACK_REASONS.has(reason)) return false;
  // Retire the GPU attempt before changing which engine owns crash markers.
  stop(); desktopCpuFallback = true; unsupported = false;
  previousInterruption = interrupted(); repeatedInterruption = previousInterruption && recoveryAttempted();
  lastFailureReason = undefined; lastProgress = {phase:"loading",downloadMB:176};
  reportClientError("cleanup_cpu_fallback",undefined,undefined,reason);
  return true;
}

function supported() {
  if (repeatedInterruption) {
    reportClientError("cleanup_previous_interruption", undefined, undefined, "previous_interruption");
    throw new InterruptedCleanupError();
  }
  const reason: CleanupFailureReason | undefined = unsupported ? unsupportedReason
    : !globalThis.isSecureContext ? "insecure_context" : typeof Worker === "undefined" ? "worker_api_missing"
    : !cpuCleanup() && !navigator.gpu ? "webgpu_api_missing"
    : typeof OffscreenCanvas === "undefined" ? "canvas_api_missing"
    : typeof createImageBitmap !== "function" ? "bitmap_api_missing" : undefined;
  if (reason) {
    reportClientError(cpuCleanup() ? "cleanup_cpu_unsupported" : "cleanup_gpu_unsupported", undefined, undefined, reason);
    if (fallbackToCpu(reason)) {supported();return;}
    throw new UnsupportedCleanupError();
  }
}
function waitFor<T>(target: Worker, match: (reply: CleanupReply) => T | undefined, timeout: number, timeoutReason: CleanupFailureReason): Promise<T> {
  return new Promise((resolve,reject) => {
    const timer = setTimeout(() => fail(new Error("Background cleanup timed out. You can use Original."),timeoutReason),timeout);
    const cancel = () => {finish(); reject(abortError());};
    waiting.add(cancel);
    function finish() {waiting.delete(cancel); clearTimeout(timer); target.removeEventListener("message",message); target.removeEventListener("error",crash);}
    function fail(error: Error, reason: CleanupFailureReason) {
      lastFailureReason = reason;
      reportClientError(error instanceof UnsupportedCleanupError ? (cpuCleanup() ? "cleanup_cpu_unsupported" : "cleanup_gpu_unsupported") : "cleanup_runtime", error, undefined, reason);
      finish(); stop(); reject(error);
    }
    function crash() {fail(new Error("Background cleanup stopped unexpectedly. You can use Original."),"worker_crash");}
    function message(event: MessageEvent<CleanupReply>) {
      const reply = event.data;
      if (reply.type === "progress") {lastProgress = reply.progress; progress?.(reply.progress); return;}
      if (reply.type === "error") {
        if (reply.kind === "unsupported") {unsupported = true;unsupportedReason = reply.reason ?? "runtime_unavailable";}
        fail(reply.kind === "unsupported" ? new UnsupportedCleanupError() : new Error(reply.message),reply.reason ?? "worker_error"); return;
      }
      const value = match(reply);
      if (value !== undefined) {running = false; markRunning(false); if (reply.type === "result") clearRecovery(); finish(); resolve(value);}
    }
    target.addEventListener("message",message); target.addEventListener("error",crash);
  });
}
function initialize() {
  supported(); clearTimeout(idle);
  if (!ready) {
    if (previousInterruption) {
      reportClientError("cleanup_retry_after_interruption", undefined, undefined, "previous_interruption");
      try {sessionStorage.setItem(recoveryKey(),"1");} catch { /* Storage is optional. */ }
    }
    running = true; markRunning(true);
    const epoch=generation;
    if (!prefetch) lastProgress = {phase:"loading",downloadMB:cpuCleanup() ? 176 : 99};
    ready=timeClientStage("cleanup_model_load", async()=>{
      // Finish the download-only warmup before the worker requests the same file.
      if (cpuCleanup() && prefetch) await prefetch.catch(()=>undefined);
      if (generation!==epoch) throw abortError();
      try {worker=cpuCleanup()?new Worker(CPU_RUNTIME_PATH+"referenceCleanup.cpu.js")
        :new Worker(new URL("../workers/referenceCleanup.worker.ts",import.meta.url),{type:"module"});}
      catch (error) {running = false;markRunning(false);reportClientError("cleanup_runtime",error,undefined,"worker_start_failed");throw new Error("Background cleanup could not start. You can use Original.");}
      const loaded=waitFor(worker,reply=>reply.type==="ready"?true:undefined,cpuCleanup()?300_000:90_000,"model_load_timeout");
      worker.postMessage({type:"initialize"} satisfies CleanupRequest);
      await loaded;
    });
  }
  return ready;
}
// On iOS, warm only the HTTP cache while the user trims. Stream and discard
// chunks instead of retaining a 176 MB JS buffer or initializing an idle model.
export function warmReferenceCleanup(): void {
  try {
    supported();
    if (!cpuCleanup()) {void initialize().then(releaseLater,()=>undefined);return;}
    if (prefetch) return;
    prefetch=timeClientStage("cleanup_model_prefetch", async()=>{
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),300_000);
      try {
        let cached:Response|undefined;
        try {cached=await (await caches.open(CPU_MODEL_CACHE)).match(CPU_MODEL_PATH);} catch { /* Optional cache. */ }
        if (cached) {lastProgress={phase:"loading",percent:100,downloadMB:176};return;}
        const response=await fetch(CPU_MODEL_PATH,{cache:"force-cache",credentials:"omit",signal:controller.signal});
        if (!response.ok || !response.body) throw new Error("Model warmup unavailable");
        let length=0,previous=-1;
        function count(value:Uint8Array) {
          length+=value.length;
          if(length>176_240_148)throw new Error("Invalid model warmup");
          const percent=Math.floor(length/176_240_148*100);
          if(percent!==previous){previous=percent;lastProgress={phase:"loading",percent,downloadMB:176};progress?.(lastProgress);}
        }
        let cache:Cache|undefined;
        try {cache=await caches.open(CPU_MODEL_CACHE);} catch { /* HTTP caching is the fallback. */ }
        if (cache && !cached && typeof TransformStream!=="undefined") {
          // Consume once directly into disk cache: no clone/tee that can buffer
          // an entire model while another consumer falls behind.
          const stream=response.body.pipeThrough(new TransformStream<Uint8Array,Uint8Array>({transform(value,output){count(value);output.enqueue(value);},flush(){if(length!==176_240_148)throw new Error("Incomplete model warmup");}}));
          await cache.put(CPU_MODEL_PATH,new Response(stream,{headers:response.headers}));
        } else {
          const reader=response.body.getReader();
          while (true) {const {done,value}=await reader.read();if(done)break;count(value);}
        }
        if(length!==176_240_148)throw new Error("Incomplete model warmup");
      } finally {clearTimeout(timer);}
    });
    void prefetch.catch(()=>undefined);
  } catch { /* Unsupported devices keep Original. */ }
}
export function cleanReferenceBackground(url: string, signal: AbortSignal, _legacyEndpoint?: string,
  onProgress?: (value: CleanupProgress) => void, shouldProcess: () => boolean = () => true): Promise<Blob> {
  if (signal.aborted) return Promise.reject(abortError());
  onProgress?.({phase:"queued"});
  const queuedAt = performance.now();
  const task = queue.then(async () => {
    reportClientTiming("cleanup_queue", (performance.now() - queuedAt) / 1000);
    if (!shouldProcess()) throw new CleanupSkippedError();
    if (signal.aborted) throw abortError();
    supported(); clearTimeout(idle);
    let cancel!: () => void;
    const canceled = new Promise<never>((_,reject) => {cancel = () => {stop(); reject(abortError());};});
    signal.addEventListener("abort",cancel,{once:true});
    progress = onProgress;
    try {
      const run = async () => {
        lastFailureReason = undefined;
        const loading = initialize(); onProgress?.(lastProgress); await loading;
        if (signal.aborted) throw abortError();
        if (!shouldProcess()) throw new CleanupSkippedError();
        // Photo pixels remain local; timing reports contain only stage names and durations.
        if (!url.startsWith("blob:")) throw new Error("Choose a local reference image to clean.");
        const response = await fetch(url,{signal});
        if (!response.ok) throw new Error("The original reference could not be read.");
        const image = await response.blob();
        if (!image.size || image.size > 5*1024*1024) throw new Error("Choose a reference smaller than 5 MiB.");
        if (signal.aborted || !worker) throw abortError();
        const id = ++sequence;
        const result = waitFor(worker, reply => reply.type === "result" && reply.id === id ? reply.image : undefined,cpuCleanup() ? 60_000 : 30_000,"inference_timeout");
        running = true; markRunning(true);
        worker.postMessage({type:"clean",id,image} satisfies CleanupRequest);
        const cleaned = await timeClientStage("cleanup_inference", () => result);
        if (!(cleaned instanceof Blob) || cleaned.type !== "image/png" || !cleaned.size || cleaned.size > 5*1024*1024)
          throw new Error("The cleaned reference could not be read. Use Original.");
        return cleaned;
      };
      const withFallback = async () => {
        try {return await run();}
        catch (error) {
          if (!signal.aborted && shouldProcess() && fallbackToCpu(lastFailureReason)) return run();
          throw error;
        }
      };
      return await Promise.race([withFallback(),canceled]);
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
