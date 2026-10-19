import {afterEach,beforeEach,expect,it,vi} from "vitest";
import type {CleanupReply,CleanupRequest} from "../browserCleanupProtocol";
class TestWorker extends EventTarget {
  static all: TestWorker[]=[];
  messages: CleanupRequest[]=[];
  terminate=vi.fn();
  constructor(){super();TestWorker.all.push(this);}
  postMessage(message: CleanupRequest){this.messages.push(message);}
  reply(data: CleanupReply){this.dispatchEvent(new MessageEvent("message",{data}));}
}
const image=()=>new Blob(["photo"],{type:"image/jpeg"});
async function settle(){for(let i=0;i<20;i++)await Promise.resolve();}
beforeEach(()=>{
  sessionStorage.removeItem("recastfx-cleanup-interrupted-v1");
  vi.resetModules();vi.useFakeTimers();TestWorker.all=[];
  vi.stubGlobal("Worker",TestWorker);vi.stubGlobal("isSecureContext",true);
  vi.stubGlobal("OffscreenCanvas",class {});vi.stubGlobal("createImageBitmap",vi.fn());
  Object.defineProperty(navigator,"gpu",{configurable:true,value:{}});
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,blob:async()=>image()}));
});
afterEach(()=>{sessionStorage.removeItem("recastfx-cleanup-interrupted-v1");vi.clearAllTimers();vi.useRealTimers();vi.unstubAllGlobals();delete (navigator as unknown as {gpu?:unknown}).gpu;});
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
])("blocks %s before prewarming or starting GPU work",async(_name,userAgent,platform,maxTouchPoints)=>{
  vi.stubGlobal("navigator",{userAgent,platform,maxTouchPoints,gpu:{}});
  const {warmReferenceCleanup,cleanReferenceBackground}=await import("../referenceCleanup");
  warmReferenceCleanup();
  await expect(cleanReferenceBackground("blob:a",new AbortController().signal)).rejects.toMatchObject({name:"UnsupportedCleanupError"});
  expect(TestWorker.all).toHaveLength(0);expect(fetch).not.toHaveBeenCalled();
});
it("does not retry a model that was interrupted by a page reload",async()=>{
  sessionStorage.setItem("recastfx-cleanup-interrupted-v1","1");
  const {warmReferenceCleanup,cleanReferenceBackground}=await import("../referenceCleanup");
  warmReferenceCleanup();
  await expect(cleanReferenceBackground("blob:a",new AbortController().signal)).rejects.toMatchObject({name:"UnsupportedCleanupError"});
  expect(TestWorker.all).toHaveLength(0);expect(fetch).not.toHaveBeenCalled();
});
