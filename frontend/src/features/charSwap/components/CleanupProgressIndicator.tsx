import {useEffect,useState} from "react";
import {CircularProgress,Stack,Typography} from "@mui/material";
import type {CleanupProgress} from "../services/browserCleanupProtocol";

// Inference exposes no internal milestones. Keep its elapsed-time estimate
// explicitly labeled and below 100 until the actual output is available.
export function CleanupProgressIndicator({progress,complete=false,label="Cleaning background"}:{progress?:CleanupProgress;complete?:boolean;label?:string}) {
  const [now,setNow]=useState(Date.now);
  useEffect(()=>{
    if (complete || progress?.phase!=="cleaning") return;
    setNow(Date.now());
    const timer=setInterval(()=>setNow(Date.now()),500);
    return ()=>clearInterval(timer);
  },[complete,progress?.phase,progress?.startedAt]);
  const estimated=progress?.phase==="cleaning"&&!complete;
  const percent=complete?100:estimated?Math.min(95,Math.max(0,Math.floor((now-(progress.startedAt ?? now))/(progress.expectedMs ?? 15000)*90)))
    :progress?.phase==="loading"?progress.percent:0;
  const text=complete?"Ready · 100%":estimated?`${label} · ${percent}% (estimated)`
    :progress?.phase==="loading"?`Loading cleanup model${percent===undefined?"":` · ${percent}%`}`:"Queued · 0%";
  return <Stack direction="row" spacing={1} alignItems="center" role="status">
    {!complete&&<CircularProgress size={18} variant={percent===undefined?"indeterminate":"determinate"} value={percent} aria-label={text}/>}
    <Typography variant="body2" color={complete?"success.main":"text.secondary"}>{text}</Typography>
  </Stack>;
}
