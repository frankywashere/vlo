import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useCharSwapStore } from "../../useCharSwapStore";
import { cleanReferenceBackground } from "../../services/referenceCleanup";
import { ReferenceBackground } from "../ReferenceBackground";
import { getStepBlocker } from "../../model/model";
vi.mock("../../services/referenceCleanup", () => ({cleanReferenceBackground: vi.fn()}));
function Harness() {
  const reference = useCharSwapStore(s => s.references.find(r => r.id === s.assignments.a));
  return reference ? <ReferenceBackground targetId="a" reference={reference} /> : null;
}
function assigned(id = "a") {
  const s = useCharSwapStore.getState(); return s.references.find(r => r.id === s.assignments[id])!;
}
beforeEach(() => {
  const s=useCharSwapStore.getState();s.reset();s.loadClip({id:"clip",name:"clip.mp4",url:"blob:clip",duration:5,width:640,height:480,mode:"upload"});
  for (const id of ["a","b"]) {
    useCharSwapStore.getState().selectTarget({id,name:id,color:"#75baff",anchor:{x:.5,y:.5,time:0}});
    useCharSwapStore.getState().assignReference(id,{id:"shared",name:"Photo",url:"blob:original"});
  }
  vi.stubGlobal("URL", {...URL, createObjectURL:vi.fn(()=>"blob:cleaned"), revokeObjectURL:vi.fn()});
});
afterEach(()=>{cleanup();vi.resetAllMocks();vi.unstubAllGlobals();});
it("prepares independently per person, permits Original while loading, and reuses the cutout",async()=>{
  let finish!:(image:Blob)=>void;
  vi.mocked(cleanReferenceBackground).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  const user=userEvent.setup();render(<Harness/>);
  expect(assigned().cleanup?.pending).toBe(true);expect(assigned("b").cleanup).toBeUndefined();
  expect(getStepBlocker(useCharSwapStore.getState(),"references")).toMatch(/Wait for background cleanup/);
  await user.click(screen.getByRole("button",{name:"Original"}));
  expect(getStepBlocker(useCharSwapStore.getState(),"references")).toBeNull();
  await act(async()=>{finish(new Blob(["image"],{type:"image/jpeg"}));});
  expect(assigned().url).toBe("blob:original");expect(assigned().cleanup?.cleanedUrl).toBe("blob:cleaned");
  await user.click(screen.getByRole("button",{name:"Clean background"}));
  expect(assigned().url).toBe("blob:cleaned");expect(assigned("b").url).toBe("blob:original");
  expect(cleanReferenceBackground).toHaveBeenCalledTimes(1);
});
it("ignores a late response after replacing the photo",async()=>{
  let finish!:(image:Blob)=>void;
  vi.mocked(cleanReferenceBackground).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockImplementation(()=>new Promise(()=>{}));
  render(<Harness/>);
  act(()=>useCharSwapStore.getState().assignReference("a",{id:"new",name:"New",url:"blob:new"}));
  await act(async()=>{finish(new Blob(["stale"],{type:"image/jpeg"}));});
  expect(assigned().cleanup?.originalUrl).toBe("blob:new");expect(URL.createObjectURL).not.toHaveBeenCalled();
});
it("falls back to Original after cleanup failure",async()=>{
  vi.mocked(cleanReferenceBackground).mockRejectedValue(new Error("Cleanup unavailable"));render(<Harness/>);
  expect(await screen.findByText("Cleanup unavailable")).toBeVisible();expect(assigned().cleanup?.mode).toBe("original");
  expect(assigned().url).toBe("blob:original");expect(getStepBlocker(useCharSwapStore.getState(),"references")).toBeNull();
});
it("passes the trusted configured gateway to the cleanup service",async()=>{
  const endpoint="https://recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean";
  vi.mocked(cleanReferenceBackground).mockResolvedValue(new Blob(["image"],{type:"image/jpeg"}));
  render(<ReferenceBackground targetId="a" reference={assigned()} cleanupEndpoint={endpoint}/>);
  await act(async()=>{});
  expect(cleanReferenceBackground).toHaveBeenCalledWith("blob:original",expect.any(AbortSignal),endpoint,expect.any(Function));
});

it("keeps Original usable and disables cleanup on unsupported hardware",async()=>{
  const {UnsupportedCleanupError}=await import("../../services/browserCleanupProtocol");
  vi.mocked(cleanReferenceBackground).mockRejectedValue(new UnsupportedCleanupError());
  render(<Harness/>);
  expect(await screen.findByText(/isn’t supported on your hardware/)).toBeVisible();
  expect(screen.getByRole("button",{name:"Clean background"})).toBeDisabled();
  expect(screen.getByRole("button",{name:"Original"})).toBeEnabled();
  expect(assigned().url).toBe("blob:original");
  expect(getStepBlocker(useCharSwapStore.getState(),"references")).toBeNull();
});
