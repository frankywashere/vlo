import {Alert,Box,Button,Chip,Stack,Typography} from "@mui/material";
import ArrowBackRounded from "@mui/icons-material/ArrowBackRounded";
import CheckRounded from "@mui/icons-material/CheckRounded";
import {targetReferences} from "../model/referenceGroups";
import type {ReferenceAsset,TargetPerson} from "../model/model";
import {CleanupProgressIndicator} from "./CleanupProgressIndicator";
import {useReferenceCleanupDraft} from "./ReferenceCleanupProvider";

interface ReferenceCleanupWaitProps {
  targets:TargetPerson[];
  references:ReferenceAsset[];
  assignments:Record<string,string>;
  referenceGroups?:Record<string,string[]>;
  onBack:()=>void;
}

export function ReferenceCleanupWait({targets,references,assignments,referenceGroups,onBack}:ReferenceCleanupWaitProps) {
  const {progress,choose}=useReferenceCleanupDraft();
  const selected=targets.flatMap(target=>targetReferences({targets,references,assignments,referenceGroups},target.id)
    .map((reference,angle)=>({target,reference,angle:angle+1}))
    .filter(item=>item.reference.cleanup?.mode==="clean"||item.reference.cleanup?.error));
  const pending=selected.filter(item=>item.reference.cleanup?.mode==="clean"&&item.reference.cleanup.pending);
  // Work can start on a later queued photo first. Keep the actual active photo
  // prominent instead of letting an earlier waiting item hide its progress.
  const active=pending.find(item=>progress[item.reference.id]?.phase==="cleaning")
    ?? pending.find(item=>progress[item.reference.id]?.phase==="loading")
    ?? pending[0] ?? selected.find(item=>item.reference.cleanup?.error) ?? selected[0];
  const activeProgress=active?progress[active.reference.id]:undefined;
  const failures=selected.filter(item=>item.reference.cleanup?.error);
  const label=(item:typeof selected[number])=>`${item.target.name} · Angle ${item.angle}`;
  function status(item:typeof selected[number]) {
    if(item.reference.cleanup?.error)return "Needs attention";
    if(!item.reference.cleanup?.pending&&item.reference.cleanup?.cleanedUrl)return "Ready";
    return progress[item.reference.id]?.phase==="cleaning"||progress[item.reference.id]?.phase==="loading"?"Processing":"Waiting";
  }
  return <Stack spacing={2.5} sx={{maxWidth:600,width:"100%",mx:"auto",py:{xs:.5,md:2}}}>
    {active&&<Box component="section" aria-label={`Preparing ${label(active)}`}
      sx={{border:"2px solid",borderColor:"primary.main",borderRadius:3,overflow:"hidden",bgcolor:"background.paper"}}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{px:{xs:2,md:3},pt:2,pb:1}} spacing={1}>
        <Typography fontWeight={700} sx={{minWidth:0,overflowWrap:"anywhere"}}>{label(active)}</Typography>
        <Chip label={`Photo ${selected.indexOf(active)+1} of ${selected.length}`} size="small" sx={{flexShrink:0}}/>
      </Stack>
      <Box sx={{mx:{xs:2,md:3},my:1,borderRadius:2,bgcolor:"action.hover",overflow:"hidden"}}>
        <Box component="img" src={active.reference.cleanup?.originalUrl??active.reference.url}
          alt={`Photo for ${active.target.name}, angle ${active.angle}`}
          sx={{display:"block",width:"100%",height:{xs:210,sm:280},objectFit:"contain"}}/>
      </Box>
      <Box sx={{px:{xs:2,md:3},pt:1,pb:2.5}}>
        {active.reference.cleanup?.error?<Typography fontSize={20} fontWeight={700}>This photo needs attention</Typography>
          :<CleanupProgressIndicator progress={activeProgress}
            complete={!active.reference.cleanup?.pending&&Boolean(active.reference.cleanup?.cleanedUrl)}
            label="Removing background" variant="prominent"/>}
        {activeProgress?.phase==="loading"&&<Typography variant="body2" color="text.secondary" sx={{mt:1.5}}>
          First use downloads {activeProgress.downloadMB??99} MB. Photos stay in your browser.
        </Typography>}
      </Box>
    </Box>}
    {selected.length>1&&<Box component="ul" aria-label="Photo preparation queue"
      sx={{display:"grid",gridTemplateColumns:{xs:"repeat(2,minmax(0,1fr))",sm:"repeat(3,minmax(0,1fr))"},gap:1,listStyle:"none",p:0,m:0}}>
      {selected.map(item=>{
        const state=status(item);
        const current=item===active;
        return <Stack component="li" key={`${item.target.id}:${item.reference.id}`}
          direction="row" spacing={1} alignItems="center" aria-current={current?"step":undefined}
          sx={{p:1,minWidth:0,border:"1px solid",borderColor:current?"primary.main":"divider",borderRadius:2,
            bgcolor:current?"action.selected":"background.paper"}}>
          <Box component="img" src={item.reference.url} alt="" sx={{width:36,height:44,objectFit:"contain",borderRadius:1,flexShrink:0}}/>
          <Box sx={{minWidth:0}}>
            <Typography variant="caption" fontWeight={600} sx={{display:"block",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}
              title={label(item)}>{item.target.name}</Typography>
            <Stack direction="row" alignItems="center" spacing={.4}>
              {state==="Ready"&&<CheckRounded sx={{fontSize:14,color:"success.main"}}/>}
              <Typography variant="caption" color={state==="Ready"?"success.main":current?"primary.main":"text.secondary"}>Angle {item.angle} · {state}</Typography>
            </Stack>
          </Box>
        </Stack>;
      })}
    </Box>}
    {failures.map(item=><Alert severity="info" key={`${item.target.id}:${item.reference.id}`}>
      <Typography fontWeight={600}>{label(item)}</Typography>
      <Typography variant="body2">{item.reference.cleanup?.error}</Typography>
      <Stack direction="row" flexWrap="wrap" gap={1} sx={{mt:1}}>
        {!item.reference.cleanup?.unsupported&&!item.reference.cleanup?.interrupted&&<Button
          aria-label={`Retry cleanup for ${label(item)}`} onClick={()=>choose(item.target.id,"clean",item.reference.id)}>Retry cleanup</Button>}
        <Button aria-label={`Use Original for ${label(item)}`} onClick={()=>choose(item.target.id,"original",item.reference.id)}>Use Original</Button>
      </Stack>
    </Alert>)}
    <Button startIcon={<ArrowBackRounded/>} onClick={onBack} sx={{alignSelf:"flex-start",minHeight:44}}>Back to characters</Button>
  </Stack>;
}
