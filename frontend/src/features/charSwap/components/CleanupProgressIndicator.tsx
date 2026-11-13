import {useEffect,useState} from "react";
import {CircularProgress,LinearProgress,Stack,Typography} from "@mui/material";
import type {CleanupProgress} from "../services/browserCleanupProtocol";

interface CleanupProgressIndicatorProps {
  progress?:CleanupProgress;
  complete?:boolean;
  label?:string;
  variant?:"inline"|"prominent";
}

// Inference exposes no internal milestones. Keep its elapsed-time estimate
// explicitly labeled and below 100 until the actual output is available.
export function CleanupProgressIndicator({progress,complete=false,label="Cleaning background",variant="inline"}:CleanupProgressIndicatorProps) {
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
  if(variant==="prominent") {
    const action=complete?"Photo ready":estimated?label:progress?.phase==="loading"?"Loading photo tools":"Waiting to start";
    const determinate=complete||estimated||(progress?.phase==="loading"&&percent!==undefined);
    return <Stack spacing={1.25} role="status" aria-live="polite">
      <Stack direction="row" justifyContent="space-between" alignItems="baseline" gap={1}>
        <Typography fontWeight={750} sx={{fontSize:{xs:20,sm:24}}}>{action}</Typography>
        {determinate&&<Typography sx={{fontSize:20,fontWeight:700,whiteSpace:"nowrap",fontVariantNumeric:"tabular-nums"}}>
          {percent}%{estimated&&<Typography component="span" sx={{display:"block",fontSize:12,fontWeight:400,color:"text.secondary",textAlign:"right"}}>estimated</Typography>}
        </Typography>}
      </Stack>
      <LinearProgress variant={determinate?"determinate":"indeterminate"} value={percent}
        aria-label={action} aria-valuetext={determinate?`${percent}%${estimated?" estimated":""}`:undefined}
        sx={{height:12,borderRadius:6,bgcolor:"action.hover","& .MuiLinearProgress-bar":{borderRadius:6}}}/>
    </Stack>;
  }
  return <Stack direction="row" spacing={1} alignItems="center" role="status">
    {!complete&&<CircularProgress size={18} variant={percent===undefined?"indeterminate":"determinate"} value={percent} aria-label={text}/>}
    <Typography variant="body2" color={complete?"success.main":"text.secondary"}>{text}</Typography>
  </Stack>;
}
