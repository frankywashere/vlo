import {afterEach,beforeEach,expect,it,vi} from "vitest";
import type {CleanupReply,CleanupRequest} from "../../services/browserCleanupProtocol";

vi.mock("onnxruntime-web/webgpu",()=>({env:{wasm:{}},InferenceSession:{create:vi.fn()}}));
let scope:{onmessage?: (event:MessageEvent<CleanupRequest>)=>Promise<void>;postMessage:ReturnType<typeof vi.fn>};
beforeEach(()=>{
 vi.resetModules();scope={postMessage:vi.fn()};vi.stubGlobal("self",scope);
 vi.stubGlobal("OffscreenCanvas",class {});vi.stubGlobal("createImageBitmap",vi.fn());
 vi.stubGlobal("fetch",vi.fn());
});
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();});
it.each([
 [undefined,"webgpu_api_missing","unsupported"],
 [{requestAdapter:async()=>null},"gpu_adapter_missing","unsupported"],
 [{requestAdapter:async()=>({features:new Set()})},"gpu_f16_missing","unsupported"],
 [{requestAdapter:async()=>{throw new TypeError("private driver detail");}},"gpu_adapter_request","image"],
])("reports the GPU startup stage before downloading or processing a photo",async(gpu,reason,kind)=>{
 vi.stubGlobal("navigator",{gpu});
 await import("../referenceCleanup.worker");
 await scope.onmessage!({data:{type:"initialize"}} as MessageEvent<CleanupRequest>);
 const reply=scope.postMessage.mock.calls.at(-1)![0] as CleanupReply;
 expect(reply).toMatchObject({type:"error",reason,kind});
 expect(fetch).not.toHaveBeenCalled();
});
