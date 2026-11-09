import {afterEach,beforeEach,expect,it} from "vitest";
import {useCharSwapStore} from "../../useCharSwapStore";
import {getStepBlocker,hasPendingReferenceCleanup} from "../model";
import {referenceIds,submittedReferences} from "../referenceGroups";
import {validateLiveSetup} from "../../services/livePipeline";
const photo=(id:string)=>({id,name:id+".jpg",url:"blob:"+id});
beforeEach(()=>{const s=useCharSwapStore.getState();s.reset();s.loadClip({id:"clip",name:"source.mp4",url:"blob:source",duration:10,width:1280,height:720,mode:"upload"});for(const id of["a","b"])s.selectTarget({id,name:id,color:"red",anchor:{x:.3,y:.5,time:0}});});
afterEach(()=>useCharSwapStore.getState().reset());
it("keeps character groups separate when they reuse a primary photo, uploads it once and promotes another angle",()=>{
 const s=useCharSwapStore.getState();s.assignReference("a",photo("shared"));s.assignReference("b",photo("shared"));
 expect(s.addReferences("a",[photo("a-side"),photo("a-back")])).toBe(true);expect(s.addReferences("b",[photo("b-side")])).toBe(true);
 let state=useCharSwapStore.getState();const normalized=validateLiveSetup(state);
 expect(submittedReferences(state)).toHaveLength(4);expect(normalized.targets).toMatchObject([{id:"a",reference_index:0,reference_indexes:[0,1,2]},{id:"b",reference_index:0,reference_indexes:[0,3]}]);
 s.makePrimaryReference("a","a-side");state=useCharSwapStore.getState();expect(state.assignments).toEqual({a:"a-side",b:"shared"});expect(referenceIds(state,"a")).toEqual(["a-side","shared","a-back"]);
 s.clearReference("a","a-side");expect(useCharSwapStore.getState().assignments.a).toBe("shared");expect(useCharSwapStore.getState().assignments.b).toBe("shared");
});
it("enforces six total unique photos atomically while allowing reuse",()=>{
 const s=useCharSwapStore.getState();s.assignReference("a",photo("one"));expect(s.addReferences("a",[2,3,4,5,6].map(n=>photo(String(n))))).toBe(true);
 expect(s.addReferences("b",[photo("seven")])).toBe(false);expect(useCharSwapStore.getState().references).toHaveLength(6);
 expect(s.addReferences("b",[photo("one")])).toBe(true);expect(submittedReferences(useCharSwapStore.getState())).toHaveLength(6);
});
it("blocks unresolved cleanup and missing supplemental angles before submission",()=>{
 const s=useCharSwapStore.getState();s.assignReference("a",photo("one"));s.assignReference("b",photo("one"));s.addReferences("a",[{...photo("side"),cleanup:{mode:"clean",originalUrl:"blob:side",pending:true}}]);
 expect(hasPendingReferenceCleanup(useCharSwapStore.getState())).toBe(true);expect(getStepBlocker(useCharSwapStore.getState(),"references")).toMatch(/Wait for background cleanup/);
 expect(()=>validateLiveSetup(useCharSwapStore.getState())).toThrow(/cleanup/);
 useCharSwapStore.setState({referenceGroups:{a:["one","missing"]}});expect(getStepBlocker(useCharSwapStore.getState(),"references")).toMatch(/missing angle/);
});
