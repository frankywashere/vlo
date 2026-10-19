import {isIOSCleanupDevice} from "../utils/browserCleanupSupport";
import {CPU_MODEL_CACHE, CPU_MODEL_PATH, CPU_RUNTIME_PATH, CleanupSkippedError, UnsupportedCleanupError} from "./browserCleanupProtocol";
import type {CleanupProgress, CleanupReply, CleanupRequest} from "./browserCleanupProtocol";

// Retained for older gateway configuration; photos are no longer sent there.
export function isReferenceCleanupEndpoint(value: unknown): value is string {
  return value === "/api/references/clean" || value === "https://recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean";
}
let queue: Promise<void> = Promise.resolve();
let worker: Worker | undefined;
let ready: Promise<void> | undefined;
// A previous GPU interruption must not block the new CPU implementation.
const cpuCleanup = () => isIOSCleanupDevice(navigator.userAgent,navigator.platform,navigator.maxTouchPoints);
const INTERRUPTED_KEY = cpuCleanup() ? "recastfx-cleanup-cpu-interrupted-v1" : "recastfx-cleanup-interrupted-v1";
function interrupted() {try {return sessionStorage.getItem(INTERRUPTED_KEY) === "1";} catch {return false;}}
function markRunning(running: boolean) {
  try {if (running) sessionStorage.setItem(INTERRUPTED_KEY,"1"); else sessionStorage.removeItem(INTERRUPTED_KEY);} catch { /* Storage is optional. */ }
}
let unsupported = interrupted();
let sequence = 0;
let generation = 0;
let prefetch: Promise<void> | undefined;
const waiting = new Set<() => void>();
let idle: ReturnType<typeof setTimeout> | undefined;
let progress: ((value: CleanupProgress) => void) | undefined;
let lastProgress: CleanupProgress = {phase:"loading"};
const abortError = () => new DOMException("Reference cleanup was canceled.","AbortError");
function stop() {
  generation++; markRunning(false); clearTimeout(idle); worker?.terminate(); worker = undefined; ready = undefined;
  for (const cancel of [...waiting]) cancel();
}
function releaseLater() {clearTimeout(idle); idle = setTimeout(stop, 60_000);}
function supported() {
  if (unsupported || !globalThis.isSecureContext || typeof Worker === "undefined" ||
    (!cpuCleanup() && !navigator.gpu) || typeof OffscreenCanvas === "undefined" || typeof createImageBitmap !== "function")
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
      if (value !== undefined) {markRunning(false); finish(); resolve(value);}
    }
    target.addEventListener("message",message); target.addEventListener("error",crash);
  });
}
function initialize() {
  supported(); clearTimeout(idle);
  if (!ready) {
    markRunning(true);
    const epoch=generation;
    if (!prefetch) lastProgress = {phase:"loading",downloadMB:cpuCleanup() ? 176 : 99};
    ready=(async()=>{
      // Finish the download-only warmup before the worker requests the same file.
      if (cpuCleanup() && prefetch) await prefetch.catch(()=>undefined);
      if (generation!==epoch) throw abortError();
      try {worker=cpuCleanup()?new Worker(CPU_RUNTIME_PATH+"referenceCleanup.cpu.js")
        :new Worker(new URL("../workers/referenceCleanup.worker.ts",import.meta.url),{type:"module"});}
      catch {markRunning(false);unsupported=true;throw new UnsupportedCleanupError();}
      const loaded=waitFor(worker,reply=>reply.type==="ready"?true:undefined,cpuCleanup()?300_000:90_000);
      worker.postMessage({type:"initialize"} satisfies CleanupRequest);
      await loaded;
    })();
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
    prefetch=(async()=>{
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
    })();
    void prefetch.catch(()=>undefined);
  } catch { /* Unsupported devices keep Original. */ }
}
export function cleanReferenceBackground(url: string, signal: AbortSignal, _legacyEndpoint?: string,
  onProgress?: (value: CleanupProgress) => void, shouldProcess: () => boolean = () => true): Promise<Blob> {
  if (signal.aborted) return Promise.reject(abortError());
  onProgress?.({phase:"queued"});
  const task = queue.then(async () => {
    if (!shouldProcess()) throw new CleanupSkippedError();
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
        if (!shouldProcess()) throw new CleanupSkippedError();
        // Only local draft blobs are read. No authenticated POST or cloud fallback.
        if (!url.startsWith("blob:")) throw new Error("Choose a local reference image to clean.");
        const response = await fetch(url,{signal});
        if (!response.ok) throw new Error("The original reference could not be read.");
        const image = await response.blob();
        if (!image.size || image.size > 5*1024*1024) throw new Error("Choose a reference smaller than 5 MiB.");
        if (signal.aborted || !worker) throw abortError();
        const id = ++sequence;
        const result = waitFor(worker, reply => reply.type === "result" && reply.id === id ? reply.image : undefined,cpuCleanup() ? 60_000 : 30_000);
        markRunning(true);
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
