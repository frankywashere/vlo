import {act,cleanup,render,screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {useCharSwapStore} from "../../useCharSwapStore";
import {cleanReferenceBackground} from "../../services/referenceCleanup";
import {InterruptedCleanupError} from "../../services/browserCleanupProtocol";
import {ReferenceBackground} from "../ReferenceBackground";
import {ReferenceCleanupProvider} from "../ReferenceCleanupProvider";
import {getStepBlocker} from "../../model/model";
vi.mock("../../services/referenceCleanup",()=>({cleanReferenceBackground:vi.fn()}));
function assigned(id="a"){const s=useCharSwapStore.getState();return s.references.find(r=>r.id===s.assignments[id])!;}
function Card(){const reference=useCharSwapStore(s=>s.references.find(r=>r.id===s.assignments.a));return reference?<ReferenceBackground targetId="a" reference={reference}/>:null;}
function Harness({show=true,endpoint}:{show?:boolean;endpoint?:string}){return <ReferenceCleanupProvider enabled endpoint={endpoint}>{show&&<Card/>}</ReferenceCleanupProvider>;}
beforeEach(()=>{
 const s=useCharSwapStore.getState();s.reset();s.loadClip({id:"clip",name:"clip.mp4",url:"blob:clip",duration:5,width:640,height:480,mode:"upload"});
 s.selectTarget({id:"a",name:"Person A",color:"#75baff",anchor:{x:.5,y:.5,time:0}});
 s.assignReference("a",{id:"photo",name:"Photo",url:"blob:original"});
 vi.stubGlobal("URL",{...URL,createObjectURL:vi.fn(()=>"blob:cleaned"),revokeObjectURL:vi.fn()});
});
afterEach(()=>{cleanup();vi.resetAllMocks();vi.unstubAllGlobals();});
it("uses Original immediately without aborting an active cleanup and reuses its result",async()=>{
 let finish!:(image:Blob)=>void;vi.mocked(cleanReferenceBackground).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 const user=userEvent.setup();render(<Harness/>);
 expect(assigned().cleanup?.pending).toBe(true);
 expect(getStepBlocker(useCharSwapStore.getState(),"references")).toMatch(/Wait for background cleanup/);
 const signal=vi.mocked(cleanReferenceBackground).mock.calls[0][1];
 await user.click(screen.getByRole("button",{name:"Original"}));
 expect(signal.aborted).toBe(false);expect(getStepBlocker(useCharSwapStore.getState(),"references")).toBeNull();
 expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
 await act(async()=>finish(new Blob(["image"],{type:"image/png"})));
 expect(assigned().url).toBe("blob:original");expect(assigned().cleanup?.cleanedUrl).toBe("blob:cleaned");
 await user.click(screen.getByRole("button",{name:"Clean background"}));
 expect(assigned().url).toBe("blob:cleaned");expect(cleanReferenceBackground).toHaveBeenCalledTimes(1);
});
it("keeps cleanup alive when the character card unmounts",async()=>{
 let finish!:(image:Blob)=>void;vi.mocked(cleanReferenceBackground).mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 const view=render(<Harness/>);const signal=vi.mocked(cleanReferenceBackground).mock.calls[0][1];
 view.rerender(<Harness show={false}/>);expect(signal.aborted).toBe(false);
 await act(async()=>finish(new Blob(["image"],{type:"image/png"})));
 expect(assigned().url).toBe("blob:cleaned");expect(assigned().cleanup?.pending).toBeFalsy();
});
it("ignores a late response after replacing the photo",async()=>{
 let finish!:(image:Blob)=>void;vi.mocked(cleanReferenceBackground).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockImplementation(()=>new Promise(()=>{}));
 render(<Harness/>);const signal=vi.mocked(cleanReferenceBackground).mock.calls[0][1];
 act(()=>useCharSwapStore.getState().assignReference("a",{id:"new",name:"New",url:"blob:new"}));
 expect(signal.aborted).toBe(true);await act(async()=>finish(new Blob(["stale"],{type:"image/png"})));
 expect(assigned().cleanup?.originalUrl).toBe("blob:new");expect(URL.createObjectURL).not.toHaveBeenCalled();
});
it("schedules each character independently and checks the current Original choice before processing",async()=>{
 vi.mocked(cleanReferenceBackground).mockImplementation(()=>new Promise(()=>{}));
 useCharSwapStore.getState().selectTarget({id:"b",name:"Person B",color:"#75baff",anchor:{x:.5,y:.5,time:0}});
 useCharSwapStore.getState().assignReference("b",{id:"photo",name:"Photo",url:"blob:original"});
 const user=userEvent.setup();render(<Harness/>);expect(cleanReferenceBackground).toHaveBeenCalledTimes(2);
 await user.click(screen.getByRole("button",{name:"Original"}));
 expect(vi.mocked(cleanReferenceBackground).mock.calls[0][4]?.()).toBe(false);
 expect(vi.mocked(cleanReferenceBackground).mock.calls[1][4]?.()).toBe(true);
 expect(assigned("b").cleanup?.mode).toBe("clean");
});
it("shows failure and lets Original acknowledge it",async()=>{
 vi.mocked(cleanReferenceBackground).mockRejectedValue(new Error("Cleanup unavailable"));render(<Harness/>);
 expect(await screen.findByText("Cleanup unavailable")).toBeVisible();
 await userEvent.setup().click(screen.getByRole("button",{name:"Original"}));
 expect(assigned().cleanup?.error).toBeUndefined();expect(assigned().url).toBe("blob:original");
});
it("passes the configured gateway to the cleanup service",async()=>{
 const endpoint="https://recastfx-api-63lqoskl5q-uk.a.run.app/api/references/clean";
 vi.mocked(cleanReferenceBackground).mockResolvedValue(new Blob(["image"],{type:"image/png"}));
 render(<Harness endpoint={endpoint}/>);await act(async()=>{});
 expect(cleanReferenceBackground).toHaveBeenCalledWith("blob:original",expect.any(AbortSignal),endpoint,expect.any(Function),expect.any(Function));
});
it("keeps Original usable on unsupported hardware",async()=>{
 const {UnsupportedCleanupError}=await import("../../services/browserCleanupProtocol");
 vi.mocked(cleanReferenceBackground).mockRejectedValue(new UnsupportedCleanupError());render(<Harness/>);
 expect(await screen.findByText(/isn’t supported on your hardware/)).toBeVisible();
 expect(screen.getByRole("button",{name:"Clean background"})).toBeDisabled();
 expect(screen.getByRole("button",{name:"Original"})).toBeEnabled();
});


it("uses Original after repeated interruptions without offering Retry or claiming unsupported hardware",async()=>{
 vi.mocked(cleanReferenceBackground).mockRejectedValueOnce(new InterruptedCleanupError());
 render(<Harness/>);
 expect(await screen.findByText(/Cleanup stopped repeatedly/)).toBeVisible();
 expect(screen.queryByRole("button",{name:"Retry cleanup"})).not.toBeInTheDocument();
 expect(screen.getByRole("button",{name:"Clean background"})).toBeDisabled();
 expect(screen.getByRole("button",{name:"Original"})).toBeEnabled();
 expect(assigned().cleanup?.unsupported).toBe(false);
});
it("adding the next character photo immediately does not cancel the first cleanup",async()=>{
 const finishes:Array<(image:Blob)=>void>=[];
 vi.mocked(cleanReferenceBackground).mockImplementation(()=>new Promise(resolve=>finishes.push(resolve)));
 render(<Harness/>);
 const firstSignal=vi.mocked(cleanReferenceBackground).mock.calls[0][1];
 act(()=>{
  useCharSwapStore.getState().selectTarget({id:"b",name:"Person B",color:"#75baff",anchor:{x:.5,y:.5,time:0}});
  useCharSwapStore.getState().assignReference("b",{id:"photo-b",name:"Second photo",url:"blob:second"});
 });
 expect(cleanReferenceBackground).toHaveBeenCalledTimes(2);
 expect(firstSignal.aborted).toBe(false);
 expect(assigned("a").cleanup?.originalUrl).toBe("blob:original");
 expect(assigned("b").cleanup?.originalUrl).toBe("blob:second");
 await act(async()=>{for(const finish of finishes)finish(new Blob(["cleaned"],{type:"image/png"}));});
 expect(assigned("a").cleanup?.pending).toBeFalsy();
 expect(assigned("b").cleanup?.pending).toBeFalsy();
 expect(firstSignal.aborted).toBe(false);
});
