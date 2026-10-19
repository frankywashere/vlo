import {afterEach,beforeEach,expect,it,vi} from "vitest";
import type {CleanupReply,CleanupRequest} from "../browserCleanupProtocol";
class TestWorker extends EventTarget {
  static all: TestWorker[]=[];
  messages: CleanupRequest[]=[];
  terminate=vi.fn();
  url: string | URL;
  options?: WorkerOptions;
  constructor(url: string | URL, options?: WorkerOptions){super();this.url=url;this.options=options;TestWorker.all.push(this);}
  postMessage(message: CleanupRequest){this.messages.push(message);}
  reply(data: CleanupReply){this.dispatchEvent(new MessageEvent("message",{data}));}
}
const image=()=>new Blob(["photo"],{type:"image/png"});
async function settle(){for(let i=0;i<20;i++)await Promise.resolve();}
beforeEach(()=>{
  sessionStorage.removeItem("recastfx-cleanup-cpu-recovery-v1"); sessionStorage.removeItem("recastfx-cleanup-interrupted-v1"); sessionStorage.removeItem("recastfx-cleanup-cpu-interrupted-v1");
  vi.resetModules();vi.useFakeTimers();TestWorker.all=[];
  vi.stubGlobal("Worker",TestWorker);vi.stubGlobal("isSecureContext",true);
  vi.stubGlobal("OffscreenCanvas",class {});vi.stubGlobal("createImageBitmap",vi.fn());
  Object.defineProperty(navigator,"gpu",{configurable:true,value:{}});
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,blob:async()=>image()}));
});
afterEach(()=>{sessionStorage.removeItem("recastfx-cleanup-cpu-recovery-v1"); sessionStorage.removeItem("recastfx-cleanup-interrupted-v1"); sessionStorage.removeItem("recastfx-cleanup-cpu-interrupted-v1");vi.clearAllTimers();vi.useRealTimers();vi.unstubAllGlobals();delete (navigator as unknown as {gpu?:unknown}).gpu;});
it("rejects unsupported browsers before reading any photo or creating a worker",async()=>{
  Object.defineProperty(navigator,"gpu",{configurable:true,value:undefined});
  const {cleanReferenceBackground}=await import("../referenceCleanup");
  await expect(cleanReferenceBackground("blob:a",new AbortController().signal)).rejects.toMatchObject({name:"UnsupportedCleanupError"});
  expect(TestWorker.all).toHaveLength(0);expect(fetch).not.toHaveBeenCalled();
});
it("warms once, serializes photos, skips canceled queued photos, and never uploads them",async()=>{
  const {warmReferenceCleanup,cleanReferenceBackground}=await import("../referenceCleanup");
  warmReferenceCleanup();warmReferenceCleanup();
  expect(TestWorker.all).toHaveLength(1);
  const worker=TestWorker.all[0];expect(worker.messages).toEqual([{type:"initialize"}]);
  const a=cleanReferenceBackground("blob:a",new AbortController().signal);
  const removed=new AbortController();
  const b=cleanReferenceBackground("blob:b",removed.signal);const rejected=expect(b).rejects.toMatchObject({name:"AbortError"});
  const c=cleanReferenceBackground("blob:c",new AbortController().signal);
  removed.abort();worker.reply({type:"ready"});await settle();
  const first=worker.messages.at(-1)!;expect(first.type).toBe("clean");
  expect(fetch).toHaveBeenCalledTimes(1);
  if(first.type!=="clean")throw Error("missing image");
  worker.reply({type:"result",id:first.id,image:image()});await a;await rejected;await settle();
  expect(vi.mocked(fetch).mock.calls.map(call=>call[0])).toEqual(["blob:a","blob:c"]);
  expect(vi.mocked(fetch).mock.calls.every(call=>!call[1]?.method)).toBe(true);
  const next=worker.messages.at(-1)!;if(next.type!=="clean")throw Error("missing image");
  worker.reply({type:"result",id:next.id,image:image()});await c;
  await vi.advanceTimersByTimeAsync(60_000);expect(worker.terminate).toHaveBeenCalledOnce();
});
it("active cancellation terminates the worker and cannot poison the next photo",async()=>{
  const {cleanReferenceBackground}=await import("../referenceCleanup");
  const abort=new AbortController();const first=cleanReferenceBackground("blob:a",abort.signal);
  const rejected=expect(first).rejects.toMatchObject({name:"AbortError"});await settle();
  const old=TestWorker.all[0];abort.abort();await rejected;
  const second=cleanReferenceBackground("blob:b",new AbortController().signal);await settle();
  const current=TestWorker.all[1];expect(old.terminate).toHaveBeenCalled();
  old.reply({type:"ready"});current.reply({type:"ready"});await settle();
  const request=current.messages.at(-1)!;if(request.type!=="clean")throw Error("missing image");
  current.reply({type:"result",id:request.id,image:image()});await second;
  await vi.advanceTimersByTimeAsync(90_000);expect(TestWorker.all).toHaveLength(2);
});
it("remembers GPU runtime incompatibility but allows download failures to retry",async()=>{
  const {cleanReferenceBackground}=await import("../referenceCleanup");
  const a=cleanReferenceBackground("blob:a",new AbortController().signal);
  const rejected=expect(a).rejects.toThrow("download failed");await settle();
  TestWorker.all[0].reply({type:"error",kind:"download",message:"download failed"});await rejected;
  const b=cleanReferenceBackground("blob:b",new AbortController().signal);
  const unsupported=expect(b).rejects.toMatchObject({name:"UnsupportedCleanupError"});await settle();
  TestWorker.all[1].reply({type:"error",kind:"unsupported",message:"unsupported"});await unsupported;
  await expect(cleanReferenceBackground("blob:c",new AbortController().signal)).rejects.toMatchObject({name:"UnsupportedCleanupError"});
  expect(TestWorker.all).toHaveLength(2);expect(fetch).not.toHaveBeenCalled();
});
it("bounds initialization and rejects invalid outputs without blocking future use",async()=>{
  const {cleanReferenceBackground}=await import("../referenceCleanup");
  const a=cleanReferenceBackground("blob:a",new AbortController().signal);const rejected=expect(a).rejects.toThrow(/timed out/);
  await settle();await vi.advanceTimersByTimeAsync(90_000);await rejected;
  const b=cleanReferenceBackground("blob:b",new AbortController().signal);const invalid=expect(b).rejects.toThrow(/could not be read/);
  await settle();const worker=TestWorker.all[1];worker.reply({type:"ready"});await settle();
  const request=worker.messages.at(-1)!;if(request.type!=="clean")throw Error("missing image");
  worker.reply({type:"result",id:request.id,image:new Blob(["bad"],{type:"text/html"})});await invalid;
});
it.each([
  ["iPhone Safari","Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Version/26.0 Mobile Safari/604.1","iPhone",5],
  ["iPhone Chrome","Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 CriOS/155 Mobile Safari/604.1","iPhone",5],
  ["iPad desktop mode","Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/26.0 Safari/605.1.15","MacIntel",5],
])("routes %s to CPU without prewarming or requesting a GPU",async(_name,userAgent,platform,maxTouchPoints)=>{
  vi.stubGlobal("navigator",{userAgent,platform,maxTouchPoints,gpu:{}});
  const {warmReferenceCleanup,cleanReferenceBackground}=await import("../referenceCleanup");
  warmReferenceCleanup();
  expect(TestWorker.all).toHaveLength(0);
  // A GPU failure from an older release must not disable the new CPU worker.
  const result=cleanReferenceBackground("blob:a",new AbortController().signal); await settle();
  const worker=TestWorker.all[0];
  expect(worker.url).toBe("/ml/litert-2.5.3-cpu-v3-alpha/referenceCleanup.cpu.js");
  expect(worker.options).toBeUndefined();
  worker.reply({type:"ready"});await settle();
  const request=worker.messages.at(-1)!;if(request.type!=="clean")throw Error("missing image");
  worker.reply({type:"result",id:request.id,image:image()});await result;
});
it("does not retry a model that was interrupted by a page reload",async()=>{
  sessionStorage.setItem("recastfx-cleanup-interrupted-v1","1");
  const {warmReferenceCleanup,cleanReferenceBackground}=await import("../referenceCleanup");
  warmReferenceCleanup();
  await expect(cleanReferenceBackground("blob:a",new AbortController().signal)).rejects.toMatchObject({name:"UnsupportedCleanupError"});
  expect(TestWorker.all).toHaveLength(0);expect(fetch).not.toHaveBeenCalled();
});

it("allows iOS CPU cleanup without WebGPU, with a separate five-minute download limit",async()=>{
  vi.stubGlobal("navigator",{userAgent:"iPhone",platform:"iPhone",maxTouchPoints:5});
  sessionStorage.setItem("recastfx-cleanup-interrupted-v1","1");
  const {cleanReferenceBackground}=await import("../referenceCleanup");
  const result=cleanReferenceBackground("blob:a",new AbortController().signal);
  const rejected=expect(result).rejects.toThrow(/timed out/);await settle();
  await vi.advanceTimersByTimeAsync(90_000);
  expect(TestWorker.all[0].terminate).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(210_000);await rejected;
  expect(TestWorker.all[0].terminate).toHaveBeenCalledOnce();
});
it("automatically recovers a single interrupted CPU runtime without a retry button",async()=>{
  vi.stubGlobal("navigator",{userAgent:"iPhone",platform:"iPhone",maxTouchPoints:5});
  sessionStorage.setItem("recastfx-cleanup-cpu-interrupted-v1","1");
  const {cleanReferenceBackground}=await import("../referenceCleanup");
  const result=cleanReferenceBackground("blob:a",new AbortController().signal);await settle();
  const worker=TestWorker.all[0];expect(worker.url).toContain("litert-2.5.3-cpu");
  worker.reply({type:"ready"});await settle();
  const request=worker.messages.at(-1)!;if(request.type!=="clean")throw Error("missing image");
  worker.reply({type:"result",id:request.id,image:image()});await result;
});

it("skips an Original-selected queued photo without restarting the active model",async()=>{
 const {cleanReferenceBackground}=await import("../referenceCleanup");
 const a=cleanReferenceBackground("blob:a",new AbortController().signal);
 let clean=true;
 const b=cleanReferenceBackground("blob:b",new AbortController().signal,undefined,undefined,()=>clean);
 const skipped=expect(b).rejects.toMatchObject({name:"CleanupSkippedError"});
 await settle();const worker=TestWorker.all[0];worker.reply({type:"ready"});await settle();clean=false;
 const first=worker.messages.at(-1)!;if(first.type!=="clean")throw Error("missing image");
 worker.reply({type:"result",id:first.id,image:image()});await a;await skipped;
 expect(vi.mocked(fetch).mock.calls.map(call=>call[0])).toEqual(["blob:a"]);
 expect(worker.terminate).not.toHaveBeenCalled();
 const c=cleanReferenceBackground("blob:c",new AbortController().signal);await settle();
 const next=worker.messages.at(-1)!;if(next.type!=="clean")throw Error("missing image");
 worker.reply({type:"result",id:next.id,image:image()});await c;expect(TestWorker.all).toHaveLength(1);
});
it("downloads ahead on iOS without initializing a model or creating a worker",async()=>{
 vi.stubGlobal("navigator",{userAgent:"iPhone",platform:"iPhone",maxTouchPoints:5});
 const read=vi.fn().mockResolvedValueOnce({done:false,value:{length:176240148}}).mockResolvedValueOnce({done:true});
 vi.mocked(fetch).mockResolvedValueOnce({ok:true,body:{getReader:()=>({read})}} as unknown as Response);
 const {warmReferenceCleanup,cleanReferenceBackground}=await import("../referenceCleanup");
 warmReferenceCleanup();warmReferenceCleanup();await settle();
 expect(fetch).toHaveBeenCalledTimes(1);expect(TestWorker.all).toHaveLength(0);
 expect(vi.mocked(fetch).mock.calls[0][0]).toMatch(/^\/models\/ormbg\//);
 const a=cleanReferenceBackground("blob:a",new AbortController().signal);await settle();
 const worker=TestWorker.all[0];worker.reply({type:"ready"});await settle();
 const first=worker.messages.at(-1)!;if(first.type!=="clean")throw Error("missing image");
 worker.reply({type:"result",id:first.id,image:image()});await a;
 expect(vi.mocked(fetch).mock.calls.map(call=>call[0])).toEqual([expect.stringContaining("/models/ormbg/"),"blob:a"]);
});

it("streams an iOS warmup into optional disk cache without creating a cleanup worker",async()=>{
 vi.stubGlobal("navigator",{userAgent:"iPhone",platform:"iPhone",maxTouchPoints:5});
 let stored=0;
 const put=vi.fn(async(_key:string,response:Response)=>{
  const reader=response.body!.getReader();while(true){const {done,value}=await reader.read();if(done)break;stored+=value.length;}
 });
 vi.stubGlobal("caches",{open:async()=>({match:async()=>undefined,put})});
 // A size-only stream chunk avoids allocating a real 176 MB fixture in this unit test.
 const body=new ReadableStream<Uint8Array>({start(output){output.enqueue({length:176240148} as Uint8Array);output.close();}});
 vi.mocked(fetch).mockResolvedValueOnce(new Response(body));
 const {warmReferenceCleanup}=await import("../referenceCleanup");warmReferenceCleanup();
 await settle();await settle();await settle();
 expect(put).toHaveBeenCalledOnce();expect(stored).toBe(176240148);expect(TestWorker.all).toHaveLength(0);
});
it("does not create an orphan worker if a photo is canceled during early download",async()=>{
 vi.stubGlobal("navigator",{userAgent:"iPhone",platform:"iPhone",maxTouchPoints:5});
 let finish!:(response:Response)=>void;
 vi.mocked(fetch).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
 const {warmReferenceCleanup,cleanReferenceBackground}=await import("../referenceCleanup");warmReferenceCleanup();
 const abort=new AbortController(),photo=cleanReferenceBackground("blob:a",abort.signal);
 const canceled=expect(photo).rejects.toMatchObject({name:"AbortError"});await settle();abort.abort();await canceled;
 const read=vi.fn().mockResolvedValueOnce({done:false,value:{length:176240148}}).mockResolvedValueOnce({done:true});
 finish({ok:true,body:{getReader:()=>({read})}} as unknown as Response);
 await settle();await settle();expect(TestWorker.all).toHaveLength(0);
});

it("keeps rapid iPhone photo uploads serial and clears the running flag after both finish",async()=>{
 vi.stubGlobal("navigator",{userAgent:"iPhone",platform:"iPhone",maxTouchPoints:5});
 const {cleanReferenceBackground}=await import("../referenceCleanup");
 const a=cleanReferenceBackground("blob:a",new AbortController().signal);await settle();
 const b=cleanReferenceBackground("blob:b",new AbortController().signal);await settle();
 const worker=TestWorker.all[0];expect(TestWorker.all).toHaveLength(1);
 expect(sessionStorage.getItem("recastfx-cleanup-cpu-interrupted-v1")).toBe("1");
 worker.reply({type:"ready"});await settle();
 const first=worker.messages.at(-1)!;if(first.type!=="clean")throw Error("missing image");
 expect(fetch).toHaveBeenCalledTimes(1);
 worker.reply({type:"result",id:first.id,image:image()});await a;await settle();
 const next=worker.messages.at(-1)!;if(next.type!=="clean")throw Error("missing image");
 expect(next.id).not.toBe(first.id);expect(worker.terminate).not.toHaveBeenCalled();
 worker.reply({type:"result",id:next.id,image:image()});await b;
 expect(sessionStorage.getItem("recastfx-cleanup-cpu-interrupted-v1")).toBeNull();
 expect(sessionStorage.getItem("recastfx-cleanup-cpu-recovery-v1")).toBeNull();
});
it("clears crash evidence when the browser leaves the page normally during initialization",async()=>{
 vi.stubGlobal("navigator",{userAgent:"iPhone",platform:"iPhone",maxTouchPoints:5});
 const {cleanReferenceBackground}=await import("../referenceCleanup");
 const result=cleanReferenceBackground("blob:a",new AbortController().signal);await settle();
 expect(sessionStorage.getItem("recastfx-cleanup-cpu-interrupted-v1")).toBe("1");
 globalThis.dispatchEvent(new Event("pagehide"));
 expect(sessionStorage.getItem("recastfx-cleanup-cpu-interrupted-v1")).toBeNull();
 const worker=TestWorker.all[0];worker.reply({type:"ready"});await settle();
 const request=worker.messages.at(-1)!;if(request.type!=="clean")throw Error("missing image");
 worker.reply({type:"result",id:request.id,image:image()});await result;
});
it("stops automatic recovery after repeated abnormal CPU reloads without calling hardware unsupported",async()=>{
 vi.stubGlobal("navigator",{userAgent:"iPhone",platform:"iPhone",maxTouchPoints:5});
 sessionStorage.setItem("recastfx-cleanup-cpu-interrupted-v1","1");
 sessionStorage.setItem("recastfx-cleanup-cpu-recovery-v1","1");
 const {cleanReferenceBackground}=await import("../referenceCleanup");
 await expect(cleanReferenceBackground("blob:a",new AbortController().signal)).rejects.toMatchObject({name:"InterruptedCleanupError",message:expect.stringContaining("Use Original")});
 expect(TestWorker.all).toHaveLength(0);expect(fetch).not.toHaveBeenCalled();
});
it("does not classify an unexpected worker error as unsupported hardware",async()=>{
 const {cleanReferenceBackground}=await import("../referenceCleanup");
 const a=cleanReferenceBackground("blob:a",new AbortController().signal);
 const failed=expect(a).rejects.toThrow(/stopped unexpectedly/);await settle();
 TestWorker.all[0].dispatchEvent(new Event("error"));await failed;
 const b=cleanReferenceBackground("blob:b",new AbortController().signal);await settle();
 const worker=TestWorker.all[1];worker.reply({type:"ready"});await settle();
 const request=worker.messages.at(-1)!;if(request.type!=="clean")throw Error("missing image");
 worker.reply({type:"result",id:request.id,image:image()});await b;
});
