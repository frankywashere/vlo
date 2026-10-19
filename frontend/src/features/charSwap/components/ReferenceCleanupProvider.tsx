import {createContext, useCallback, useContext, useEffect, useRef, useState} from "react";
import type {ReactNode} from "react";
import {useCharSwapStore} from "../useCharSwapStore";
import {cleanReferenceBackground} from "../services/referenceCleanup";
import {CleanupSkippedError, UnsupportedCleanupError} from "../services/browserCleanupProtocol";
import type {CleanupProgress} from "../services/browserCleanupProtocol";
import {isIOSCleanupDevice} from "../utils/browserCleanupSupport";
import {createLocalId} from "../utils/createLocalId";

type CleanupJob = {id:string; original:string; controller:AbortController};
interface CleanupDraft {
  progress:Record<string,CleanupProgress>;
  choose:(targetId:string,mode:"original"|"clean")=>void;
}
const Context = createContext<CleanupDraft | null>(null);
export function useReferenceCleanupDraft() {
  const value=useContext(Context);
  if (!value) throw new Error("Reference cleanup needs a draft owner.");
  return value;
}
function current(targetId:string) {
  const state=useCharSwapStore.getState();
  return state.references.find(item=>item.id===state.assignments[targetId]);
}
// The draft owns requests; photo cards and the waiting screen only present them.
export function ReferenceCleanupProvider({enabled,endpoint,children}:{enabled:boolean;endpoint?:string;children:ReactNode}) {
  const jobs=useRef(new Map<string,CleanupJob>());
  const alive=useRef(true);
  const [progress,setProgress]=useState<Record<string,CleanupProgress>>({});
  const prepare=useCallback((targetId:string)=>{
    const reference=current(targetId);
    if (!enabled || !reference || jobs.current.has(targetId)) return;
    const original=reference.cleanup?.originalUrl ?? reference.url;
    const id=createLocalId(), controller=new AbortController();
    jobs.current.set(targetId,{id,original,controller});
    setProgress(previous=>({...previous,[targetId]:{phase:"queued"}}));
    useCharSwapStore.getState().assignReference(targetId,{...reference,id,cleanup:{originalUrl:original,mode:"clean",pending:true}});
    void cleanReferenceBackground(original,controller.signal,endpoint,value=>{
      if (alive.current && current(targetId)?.id===id) setProgress(previous=>({...previous,[targetId]:value.phase==="cleaning"
        ?{...value,startedAt:previous[targetId]?.phase==="cleaning"?previous[targetId].startedAt:Date.now(),
          expectedMs:isIOSCleanupDevice(navigator.userAgent,navigator.platform,navigator.maxTouchPoints)?15000:8000}:value}));
    },()=>current(targetId)?.id===id && current(targetId)?.cleanup?.mode==="clean").then(image=>{
      const asset=current(targetId);
      if (controller.signal.aborted || asset?.id!==id) return;
      const cleanedUrl=URL.createObjectURL(image),mode=asset.cleanup?.mode ?? "original";
      useCharSwapStore.getState().assignReference(targetId,{...asset,url:mode==="clean"?cleanedUrl:original,
        cleanup:{originalUrl:original,cleanedUrl,mode}});
    }).catch(error=>{
      const asset=current(targetId);
      if (asset?.id!==id) return;
      useCharSwapStore.getState().assignReference(targetId,{...asset,url:original,
        cleanup:{originalUrl:original,mode:"original",pending:false,unsupported:error instanceof UnsupportedCleanupError,
          error:error instanceof CleanupSkippedError || controller.signal.aborted || asset.cleanup?.mode==="original" ? undefined : error instanceof Error ? error.message : "Cleanup did not finish. Use Original."}});
    }).finally(()=>{if (jobs.current.get(targetId)?.id===id) jobs.current.delete(targetId);});
  },[enabled,endpoint]);
  const choose=useCallback((targetId:string,mode:"original"|"clean")=>{
    const reference=current(targetId);if (!reference) return;
    const original=reference.cleanup?.originalUrl ?? reference.url;
    if (mode==="clean" && !reference.cleanup?.cleanedUrl && !reference.cleanup?.pending) {prepare(targetId);return;}
    useCharSwapStore.getState().assignReference(targetId,{...reference,
      id:reference.cleanup?.pending?reference.id:createLocalId(),
      url:mode==="clean"?reference.cleanup?.cleanedUrl ?? original:original,
      cleanup:{...reference.cleanup,originalUrl:original,mode,error:undefined}});
  },[prepare]);
  useEffect(()=>{
    alive.current=true;
    let reconciling=false;
    function reconcile() {
      if (reconciling) return;
      reconciling=true;
      try {
      for (const [targetId,job] of jobs.current) {
        if (current(targetId)?.id!==job.id) {job.controller.abort();jobs.current.delete(targetId);}
      }
      if (!enabled) return;
      for (const target of useCharSwapStore.getState().targets) {
        const reference=current(target.id);
        if (reference && !reference.cleanup) prepare(target.id);
      }
      } finally {reconciling=false;}
    }
    const unsubscribe=useCharSwapStore.subscribe(reconcile);reconcile();
    const ownedJobs=jobs.current;
    return ()=>{
      alive.current=false;unsubscribe();
      for (const [targetId,job] of ownedJobs) {
        job.controller.abort();
        const asset=current(targetId);
        if (asset?.id===job.id && asset.cleanup?.pending) useCharSwapStore.getState().assignReference(targetId,{...asset,url:job.original,
          cleanup:{...asset.cleanup,mode:"original",pending:false}});
      }
      ownedJobs.clear();
    };
  },[enabled,prepare]);
  return <Context.Provider value={{progress,choose}}>{children}</Context.Provider>;
}
