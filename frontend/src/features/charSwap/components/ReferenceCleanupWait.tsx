import {Alert,Box,Button,CircularProgress,Stack,Typography} from "@mui/material";
import ArrowBackRounded from "@mui/icons-material/ArrowBackRounded";
import type {ReferenceAsset,TargetPerson} from "../model/model";
import {useReferenceCleanupDraft} from "./ReferenceCleanupProvider";

export function ReferenceCleanupWait({targets,references,assignments,onBack}:{targets:TargetPerson[];references:ReferenceAsset[];assignments:Record<string,string>;onBack:()=>void}) {
  const {progress,choose}=useReferenceCleanupDraft();
  const selected=targets.flatMap(target=>{
    const reference=references.find(item=>item.id===assignments[target.id]);
    return reference && (reference.cleanup?.mode==="clean" || reference.cleanup?.error)?[{target,reference}]:[];
  });
  const pending=selected.find(item=>item.reference.cleanup?.mode==="clean"&&item.reference.cleanup.pending);
  const failures=selected.filter(item=>item.reference.cleanup?.error);
  const active=pending?progress[pending.target.id]:undefined;
  return <Stack spacing={3} sx={{py:{xs:2,md:5},maxWidth:640,mx:"auto"}}>
    {pending && <Box role="status" sx={{display:"flex",gap:2,alignItems:"center"}}>
      <CircularProgress/><Box>
        <Typography fontWeight={600}>Character {selected.indexOf(pending)+1} of {selected.length}</Typography>
        <Typography color="text.secondary">{active?.phase==="loading"?`Downloading cleanup model${active.percent===undefined?"":` · ${active.percent}%`}`
          :active?.phase==="cleaning"?`Cleaning ${pending.target.name}…`:"Waiting for the previous photo to finish…"}</Typography>
        {active?.phase==="loading" && <Typography variant="caption" color="text.secondary">First use downloads {active.downloadMB ?? 99} MB. Your photos stay in this browser.</Typography>}
      </Box>
    </Box>}
    {failures.map(({target,reference})=><Alert severity="info" key={target.id}>
      <Typography>{target.name}: {reference.cleanup?.error}</Typography>
      <Stack direction="row" gap={1} sx={{mt:1}}>
        {!reference.cleanup?.unsupported && <Button onClick={()=>choose(target.id,"clean")}>Retry cleanup</Button>}
        <Button onClick={()=>choose(target.id,"original")}>Use Original</Button>
      </Stack>
    </Alert>)}
    <Typography variant="body2" color="text.secondary">We’ll continue automatically when your selected images are ready. You can go back and choose Original for any character.</Typography>
    <Button startIcon={<ArrowBackRounded/>} onClick={onBack} sx={{alignSelf:"flex-start"}}>Back to characters</Button>
  </Stack>;
}
