import {cleanup,fireEvent,render,screen,within} from "@testing-library/react";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
import type {ReferenceAsset,TargetPerson} from "../../model/model";
import type {CleanupProgress} from "../../services/browserCleanupProtocol";
import {ReferenceCleanupWait} from "../ReferenceCleanupWait";

const draft=vi.hoisted(()=>({progress:{} as Record<string,CleanupProgress>,choose:vi.fn()}));
vi.mock("../ReferenceCleanupProvider",()=>({useReferenceCleanupDraft:()=>draft}));
const targets:TargetPerson[]=[1,2].map(index=>({id:`p${index}`,name:`Person ${index}`,color:"cyan",anchor:{x:.5,y:.5,time:0}}));
function photo(id:string,cleanup:ReferenceAsset["cleanup"]):ReferenceAsset {
  return {id,name:`really-long-camera-filename-${id}-with-private-details.jpg`,url:`/photo-${id}.jpg`,cleanup};
}
function props(){return {targets,references:[
  photo("one",{originalUrl:"/one-original.jpg",mode:"clean",pending:true}),
  photo("two",{originalUrl:"/two-original.jpg",cleanedUrl:"/two-cleaned.png",mode:"clean"}),
  photo("three",{originalUrl:"/three-original.jpg",mode:"clean",pending:true}),
],assignments:{p1:"one",p2:"three"},referenceGroups:{p1:["one","two"],p2:["three"]},onBack:vi.fn()};}
beforeEach(()=>{draft.progress={};draft.choose.mockReset();});
afterEach(cleanup);

it("highlights the photo actually processing instead of an earlier queued photo",()=>{
  draft.progress={one:{phase:"queued"},three:{phase:"cleaning",startedAt:Date.now()-5000,expectedMs:15000}};
  const p=props();render(<ReferenceCleanupWait {...p}/>);
  const active=screen.getByRole("region",{name:"Preparing Person 2 · Angle 1"});
  expect(within(active).getByRole("img",{name:"Photo for Person 2, angle 1"})).toHaveAttribute("src","/three-original.jpg");
  expect(within(active).getByText("Photo 3 of 3")).toBeVisible();
  expect(screen.getByRole("progressbar",{name:"Removing background"})).toBeVisible();
  const queue=screen.getByRole("list",{name:"Photo preparation queue"});
  expect(within(queue).getByText("Angle 1 · Waiting")).toBeVisible();
  expect(within(queue).getByText("Angle 2 · Ready")).toBeVisible();
  expect(within(queue).getByText("Angle 1 · Processing")).toBeVisible();
  expect(screen.queryByText(/really-long-camera-filename/)).not.toBeInTheDocument();
});

it("shows real tool-download progress and its size while prioritizing the loading photo",()=>{
  draft.progress={one:{phase:"queued"},three:{phase:"loading",percent:42,downloadMB:176}};
  render(<ReferenceCleanupWait {...props()}/>);
  expect(screen.getByText("Loading photo tools")).toBeVisible();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow","42");
  expect(screen.getByText("First use downloads 176 MB. Photos stay in your browser.")).toBeVisible();
  expect(screen.queryByText(/estimated/)).not.toBeInTheDocument();
});

it("retains targeted retry, Original and Back controls when cleanup fails",()=>{
  const p=props();p.references=[photo("one",{originalUrl:"/one.jpg",mode:"original",error:"Could not prepare this photo."})];
  p.targets=[targets[0]];p.referenceGroups={p1:["one"],p2:[]};
  render(<ReferenceCleanupWait {...p}/>);
  expect(screen.getByText("This photo needs attention")).toBeVisible();
  expect(screen.getByText("Could not prepare this photo.")).toBeVisible();
  fireEvent.click(screen.getByRole("button",{name:"Retry cleanup for Person 1 · Angle 1"}));
  expect(draft.choose).toHaveBeenLastCalledWith("p1","clean","one");
  fireEvent.click(screen.getByRole("button",{name:"Use Original for Person 1 · Angle 1"}));
  expect(draft.choose).toHaveBeenLastCalledWith("p1","original","one");
  fireEvent.click(screen.getByRole("button",{name:"Back to characters"}));
  expect(p.onBack).toHaveBeenCalledOnce();
});

it("keeps unsupported photos recoverable with Original without offering a broken retry",()=>{
  const p=props();p.references=[photo("one",{originalUrl:"/one.jpg",mode:"original",unsupported:true,error:"Not supported here."})];
  p.targets=[targets[0]];
  render(<ReferenceCleanupWait {...p}/>);
  expect(screen.queryByRole("button",{name:/Retry cleanup/})).not.toBeInTheDocument();
  expect(screen.getByRole("button",{name:/Use Original/})).toBeVisible();
});
