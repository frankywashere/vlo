import {createContext, useCallback, useContext, useEffect, useRef, useState} from "react";
import type {ReactNode} from "react";
import {useCharSwapStore} from "../useCharSwapStore";
import {referenceIds,targetReferences} from "../model/referenceGroups";
import {cleanReferenceBackground} from "../services/referenceCleanup";
import {CleanupSkippedError, InterruptedCleanupError, UnsupportedCleanupError} from "../services/browserCleanupProtocol";
import type {CleanupProgress} from "../services/browserCleanupProtocol";
import {isIOSCleanupDevice} from "../utils/browserCleanupSupport";
import {createLocalId} from "../utils/createLocalId";

type CleanupJob = {id:string; original:string; controller:AbortController};
interface CleanupDraft {
  progress:Record<string,CleanupProgress>;
  choose:(targetId:string,mode:"original"|"clean",referenceId?:string)=>void;
}
const Context = createContext<CleanupDraft | null>(null);
export function useReferenceCleanupDraft() {
  const value=useContext(Context);
  if (!value) throw new Error("Reference cleanup needs a draft owner.");
  return value;
}
function current(referenceId:string) {return useCharSwapStore.getState().references.find(item=>item.id===referenceId);}
function ownReference(targetId:string,referenceId:string){
  const state=useCharSwapStore.getState(),asset=current(referenceId);if(!asset)return;
  if(state.targets.filter(target=>referenceIds(state,target.id).includes(referenceId)).length<2)return asset;
  const clone={...asset,id:createLocalId(),...(asset.cleanup?.pending?{cleanup:{...asset.cleanup,pending:false,mode:"original" as const}}:{})};
  state.replaceReferenceForTarget(targetId,referenceId,clone);return clone;
}

// Stable asset IDs let every angle keep its group membership while cleanup runs.
export function ReferenceCleanupProvider({enabled,endpoint,children}:{enabled:boolean;endpoint?:string;children:ReactNode}) {
  const jobs=useRef(new Map<string,CleanupJob>()),alive=useRef(true);
  const cleanedUrls=useRef(new Map<string,string>());
  const [progress,setProgress]=useState<Record<string,CleanupProgress>>({});
  const prepare=useCallback((targetId:string,requestedId:string)=>{
    const reference=ownReference(targetId,requestedId);
    const referenceId=reference?.id ?? requestedId;
    if (!enabled || !reference || jobs.current.has(referenceId)) return;
    const original=reference.cleanup?.originalUrl ?? reference.url;
    const id=createLocalId(),controller=new AbortController();
    jobs.current.set(referenceId,{id,original,controller});
    const owns=()=>jobs.current.get(referenceId)?.id===id && current(referenceId)?.cleanup?.originalUrl===original;
    setProgress(previous=>({...previous,[referenceId]:{phase:"queued"}}));
    useCharSwapStore.getState().updateReferenceAsset({...reference,cleanup:{originalUrl:original,mode:"clean",pending:true}});
    void cleanReferenceBackground(original,controller.signal,endpoint,value=>{
      if (alive.current && owns()) setProgress(previous=>({...previous,[referenceId]:value.phase==="cleaning"
        ?{...value,startedAt:previous[referenceId]?.phase==="cleaning"?previous[referenceId].startedAt:Date.now(),
          expectedMs:isIOSCleanupDevice(navigator.userAgent,navigator.platform,navigator.maxTouchPoints)?15000:8000}:value}));
    },()=>owns() && current(referenceId)?.cleanup?.mode==="clean").then(image=>{
      const asset=current(referenceId);
      if (controller.signal.aborted || !owns() || !asset) return;
      const cached=cleanedUrls.current.get(original);
      const retained=cached&&useCharSwapStore.getState().references.some(reference=>reference.cleanup?.cleanedUrl===cached);
      const cleanedUrl=retained?cached:URL.createObjectURL(image),mode=asset.cleanup?.mode ?? "original";
      cleanedUrls.current.set(original,cleanedUrl);
      useCharSwapStore.getState().updateReferenceAsset({...asset,url:mode==="clean"?cleanedUrl:original,cleanup:{originalUrl:original,cleanedUrl,mode}});
    }).catch(error=>{
      const asset=current(referenceId);if (!owns() || !asset) return;
      useCharSwapStore.getState().updateReferenceAsset({...asset,url:original,
        cleanup:{originalUrl:original,mode:"original",pending:false,unsupported:error instanceof UnsupportedCleanupError,interrupted:error instanceof InterruptedCleanupError,
          error:error instanceof CleanupSkippedError || controller.signal.aborted || asset.cleanup?.mode==="original" ? undefined : error instanceof Error ? error.message : "Cleanup did not finish. Use Original."}});
    }).finally(()=>{if(jobs.current.get(referenceId)?.id===id)jobs.current.delete(referenceId);});
  },[enabled,endpoint]);
  const choose=useCallback((targetId:string,mode:"original"|"clean",requestedId?:string)=>{
    const state=useCharSwapStore.getState();
    let referenceId=requestedId ?? state.assignments[targetId];
    if (!referenceId || !referenceIds(state,targetId).includes(referenceId)) return;
    const reference=ownReference(targetId,referenceId);if(!reference)return;referenceId=reference.id;
    const original=reference.cleanup?.originalUrl ?? reference.url;
    if(mode==="clean"&&!reference.cleanup?.cleanedUrl&&!reference.cleanup?.pending){prepare(targetId,referenceId);return;}
    useCharSwapStore.getState().updateReferenceAsset({...reference,url:mode==="clean"?reference.cleanup?.cleanedUrl ?? original:original,
      cleanup:{...reference.cleanup,originalUrl:original,mode,error:undefined}});
  },[prepare]);
  useEffect(()=>{
    alive.current=true;let reconciling=false;
    function reconcile(){
      if(reconciling)return;reconciling=true;
      try {
        const state=useCharSwapStore.getState(),retained=new Set(state.targets.flatMap(target=>referenceIds(state,target.id)));
        for(const [referenceId,job]of jobs.current)if(!retained.has(referenceId)||!current(referenceId)){job.controller.abort();jobs.current.delete(referenceId);}
        if(enabled)for(const target of state.targets)for(const reference of targetReferences(useCharSwapStore.getState(),target.id))if(!reference.cleanup)prepare(target.id,reference.id);
      }finally{reconciling=false;}
    }
    const unsubscribe=useCharSwapStore.subscribe(reconcile);reconcile();const ownedJobs=jobs.current;
    return()=>{
      alive.current=false;unsubscribe();
      for(const [referenceId,job]of ownedJobs){job.controller.abort();const asset=current(referenceId);
        if(asset?.cleanup?.pending)useCharSwapStore.getState().updateReferenceAsset({...asset,url:job.original,cleanup:{...asset.cleanup,mode:"original",pending:false}});}
      ownedJobs.clear();
    };
  },[enabled,prepare]);
  return <Context.Provider value={{progress,choose}}>{children}</Context.Provider>;
}
