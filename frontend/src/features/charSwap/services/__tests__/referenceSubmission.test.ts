import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createLiveJob, validateLiveSetup } from "../livePipeline";
import { canVisitStep, createInitialState, getStepBlocker } from "../../model/model";
import type { ReferenceAsset } from "../../model/model";
const request = vi.fn<typeof fetch>();
function draft(cleanup?: ReferenceAsset["cleanup"]) {
  return {...createInitialState(), clip:{id:"clip",name:"source.mp4",url:"blob:source",duration:5,width:640,height:480,mode:"upload" as const},
    trim:[0,5] as [number,number],targets:[{id:"one",name:"Person 1",color:"#00dcff",anchor:{x:.3,y:.5,time:1}}],
    references:[{id:"ref",name:"photo.png",url:"blob:original",cleanup}],assignments:{one:"ref"}};
}
beforeEach(()=>{request.mockReset();vi.stubGlobal("fetch",request);vi.stubGlobal("location",{protocol:"https:",hostname:"recastfx.com",origin:"https://recastfx.com"});});
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();});
it.each([
  {originalUrl:"blob:original",mode:"clean" as const,pending:true},
  {originalUrl:"blob:original",mode:"clean" as const,pending:false},
  {originalUrl:"blob:original",mode:"original" as const,error:"cleanup failed"},
])("blocks unfinished or failed cleanup before any upload: %j",async(cleanup)=>{
  const state=draft(cleanup);
  expect(getStepBlocker(state,"references")).toMatch(/cleanup/i);
  expect(canVisitStep(state,"review")).toBe(false);
  await expect(createLiveJob(state,"session",new AbortController().signal)).rejects.toThrow(/cleanup/i);
  expect(request).not.toHaveBeenCalled();
});
it("waits for every selected character even after the first photo finishes",async()=>{
  const state=draft({originalUrl:"blob:original",mode:"clean",cleanedUrl:"blob:clean-one"});
  state.targets.push({...state.targets[0],id:"two",name:"Person 2"});
  state.references.push({id:"second",name:"two.png",url:"blob:two-original",cleanup:{originalUrl:"blob:two-original",mode:"clean",pending:true}});
  Object.assign(state.assignments,{two:"second"});
  await expect(createLiveJob(state,"session",new AbortController().signal)).rejects.toThrow(/Person 2/);
  expect(request).not.toHaveBeenCalled();
});
it("uploads the completed cleaned bytes and waits for uploads before confirming",async()=>{
  const state=draft({originalUrl:"blob:original",mode:"clean",cleanedUrl:"blob:cleaned"});
  const puts:File[]=[];let finishUpload!:()=>void;
  function media(text: string, type: string) {
    const response = new Response(null);
    vi.spyOn(response, "blob").mockResolvedValue(new Blob([text],{type}));
    return response;
  }
  const pending=new Promise<Response>(resolve=>{finishUpload=()=>resolve(new Response(null,{status:200}));});
  request.mockImplementation(async(input,init)=>{
    const url=String(input);
    if(url==="/api/preparation-capacity")return new Response(JSON.stringify({available:true}));
    if(url==="blob:source")return media("SOURCE","video/mp4");
    if(url==="blob:cleaned")return media("CLEANED-PHOTO","image/png");
    if(url==="blob:original")throw new Error("Original must not be used for Clean background");
    if(url==="/api/uploads")return new Response(JSON.stringify({id:"ticket",source:{url:"https://storage.googleapis.com/source",headers:{"Content-Type":"application/octet-stream","x-goog-if-generation-match":"0"}},references:[{url:"https://storage.googleapis.com/reference",headers:{"Content-Type":"application/octet-stream","x-goog-if-generation-match":"0"}}]}));
    if(url==="https://storage.googleapis.com/source")return new Response(null,{status:200});
    if(url==="https://storage.googleapis.com/reference"){puts.push(init!.body as File);return pending;}
    if(url==="/api/uploads/ticket/complete")return new Response(JSON.stringify({id:"ticket",status:"queued"}));
    throw new Error("Unexpected request");
  });
  const result=createLiveJob(state,"session",new AbortController().signal);
  void result.catch(()=>{});
  await vi.waitFor(()=>expect(puts).toHaveLength(1));
  expect(request.mock.calls.some(([url])=>url==="/api/uploads/ticket/complete")).toBe(false);
  const bytes=await new Promise<string>(resolve=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.readAsText(puts[0]);});
  expect(bytes).toBe("CLEANED-PHOTO");
  finishUpload();expect((await result).status).toBe("queued");
});
it("uses Original immediately while cleanup continues and ignores unused pending photos",()=>{
  const state=draft({originalUrl:"blob:original",mode:"original",pending:true,cleanedUrl:"blob:cleaned"});
  state.references[0].url="blob:stale-cleaned-display";
  state.references.push({id:"unused",name:"unused.png",url:"blob:unused",cleanup:{originalUrl:"blob:unused",mode:"clean",pending:true}});
  const selected=validateLiveSetup(state);
  expect(selected.references).toHaveLength(1);expect(selected.reference.url).toBe("blob:original");
});
