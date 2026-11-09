import { referenceIds, submittedReferences, MAX_REFERENCE_IMAGES } from "../model/referenceGroups";
import { useRef } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  Box,
  Button,
  ButtonBase,
  Divider,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import AddPhotoAlternateOutlined from "@mui/icons-material/AddPhotoAlternateOutlined";
import CheckRounded from "@mui/icons-material/CheckRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import { useCharSwapStore } from "../useCharSwapStore";
import { ALL_PEOPLE_TARGET_ID, type ReferenceAsset, type TargetPerson } from "../model/model";
import type { DemoManifest } from "../services/demo";
import { PersonBadge } from "./PersonBadge";
import { useFileDrop } from "../hooks/useFileDrop";
import { ReferenceUpload } from "./ReferenceUpload";
import { PANEL } from "./stepStyles";
import { ReferenceBackground } from "./ReferenceBackground";

interface ReferencesStepProps {
  demo: DemoManifest | null;
  onImport: (id: string, file: File) => void;
  onImportAdditional?: (id: string, files: File[]) => void;
  pendingIds: string[];
  cleanupEnabled?: boolean;
  cleanupEndpoint?: string;
}

interface ReferenceCardProps extends ReferencesStepProps {
  person: TargetPerson;
  reference?: ReferenceAsset;
  assignReference: (id: string, reference: ReferenceAsset) => void;
  clearReference: (id: string, referenceId?: string) => void;
}

function ReferenceCard({
  person,
  reference,
  demo,
  pendingIds,
  onImport,
  assignReference,
  clearReference,
  cleanupEnabled,
  cleanupEndpoint,
}: ReferenceCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const pending = pendingIds.includes(person.id);
  const {dragging,dropHandlers} = useFileDrop(file => onImport(person.id,file),pending);
  return (
    <Box role="group" aria-label={`Reference upload area for ${person.name}`} {...dropHandlers}
      sx={{ ...PANEL, borderLeft: `3px solid ${person.color}`, outline:dragging?"2px dashed":"none",outlineColor:"primary.main",outlineOffset:3 }}>
      <PersonBadge person={person} imageSize={160} stackedOnMobile subtitle={person.id === ALL_PEOPLE_TARGET_ID ? "One replacement character for everyone in every scene" : "Original person"} />
      <Divider sx={{ my: 2 }} />
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "220px 1fr" },
          gap: 2.5,
          alignItems: "center",
        }}
      >
        <ButtonBase
          component="button"
          type="button"
          disabled={pending}
          onClick={() => {
            if (!pending) inputRef.current?.click();
          }}
          aria-label={`${reference ? "Change" : "Upload"} reference image for ${person.name}`}
          sx={{
            width: "100%",
            height: 220,
            minWidth: 44,
            minHeight: 44,
            borderRadius: "5px",
            bgcolor: "background.default",
            border: "1px dashed",
            borderColor: "divider",
            display: "grid",
            placeItems: "center",
            overflow: "hidden",
            transition: "border-color .15s, background-color .15s",
            "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
            "&.Mui-focusVisible": {
              outline: "2px solid #cc3f37",
              outlineOffset: 3,
            },
            "&.Mui-disabled": { opacity: 0.6 },
          }}
        >
          {reference ? (
            <Box
              component="img"
              src={reference.url}
              draggable={false}
              alt={`${person.name} replacement: ${reference.name}`}
              sx={{ width: "100%", height: "100%", objectFit: "contain",
                ...(reference.cleanup?.mode === "clean" && reference.cleanup.cleanedUrl ? {
                  backgroundColor: "#fff",
                } : {}),
              }}
            />
          ) : (
            <AddPhotoAlternateOutlined
              sx={{ color: "text.secondary", fontSize: 38 }}
            />
          )}
        </ButtonBase>
        <Stack spacing={1.25} alignItems="flex-start" sx={{ minWidth: 0 }}>
          <Typography fontWeight={600}>
            {reference ? reference.name : person.id === ALL_PEOPLE_TARGET_ID ? "Your replacement character" : `Who replaces ${person.name}?`}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            The primary photo defines the character’s outfit and appearance. Add other angles below to support identity.
          </Typography>
          <Stack direction="row" spacing={1}>
            <ReferenceUpload
              person={person}
              pending={pending}
              hasReference={!!reference}
              onImport={onImport}
              inputRef={inputRef}
            />
            {reference && (
              <IconButton
                aria-label={`Remove reference for ${person.name}`}
                disabled={pending}
                onClick={() => clearReference(person.id)}
                sx={{ width: 44, height: 44 }}
              >
                <CloseRounded fontSize="small" />
              </IconButton>
            )}
          </Stack>
          {cleanupEnabled && reference && <ReferenceBackground targetId={person.id} reference={reference} cleanupEndpoint={cleanupEndpoint} />}
          {demo && (
            <Button
              size="small"
              disabled={pending}
              onClick={() => assignReference(person.id, demo.reference)}
            >
              Use sample teddy bear
            </Button>
          )}
        </Stack>
      </Box>
      {reference && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ mt: 2, color: "success.main" }}
        >
          <CheckRounded fontSize="small" />
          <Typography variant="body2">Assigned to {person.name}</Typography>
        </Stack>
      )}
    </Box>
  );
}

export function ReferencesStep({
  demo,
  onImport,
  onImportAdditional,
  pendingIds,
  cleanupEnabled = false,
  cleanupEndpoint,
}: ReferencesStepProps) {
  const { targets, references, assignments, referenceGroups, assignReference, clearReference, addReferences, makePrimaryReference } =
    useCharSwapStore(
      useShallow((s) => ({
        targets: s.targets,
        references: s.references,
        assignments: s.assignments,
        referenceGroups: s.referenceGroups,
        addReferences: s.addReferences,
        makePrimaryReference: s.makePrimaryReference,
        assignReference: s.assignReference,
        clearReference: s.clearReference,
      })),
    );
  return (
    <Stack spacing={2.5}>
      <Typography variant="body2" color="text.secondary">
        JPG, PNG and WebP photos are converted automatically. For multi-picture JPEGs, we use the main photo.
      </Typography>
      <Typography variant="body2" color="text.secondary">{submittedReferences({targets, references, assignments, referenceGroups}).length} of {MAX_REFERENCE_IMAGES} photos total · share a photo between characters without uploading it twice.</Typography>
      {targets.map((person) => (
        <Stack key={person.id} spacing={1}>
        <ReferenceCard
          key={person.id}
          person={person}
          reference={references.find(
            (item) => item.id === assignments[person.id],
          )}
          demo={demo}
          pendingIds={pendingIds}
          cleanupEnabled={cleanupEnabled}
          cleanupEndpoint={cleanupEndpoint}
          onImport={onImport}
          assignReference={assignReference}
          clearReference={clearReference}
        />
        {onImportAdditional && <ReferenceAngles targetId={person.id} name={person.name} onImport={onImportAdditional}
          pending={pendingIds.includes(person.id)} cleanupEnabled={cleanupEnabled} cleanupEndpoint={cleanupEndpoint}
          references={references} ids={referenceIds({assignments, referenceGroups}, person.id)} primary={assignments[person.id]}
          count={submittedReferences({targets, references, assignments, referenceGroups}).length}
          onRemove={referenceId => clearReference(person.id, referenceId)}
          onPrimary={referenceId => makePrimaryReference(person.id, referenceId)}
          onReuse={reference => addReferences(person.id, [reference])} />}
        </Stack>
      ))}
      <Typography variant="body2" color="text.secondary">
        {targets[0]?.id === ALL_PEOPLE_TARGET_ID
          ? "Everyone will become a separate version of this character. Their individual positions and motion stay separate."
          : "Each card connects one original person to one replacement character. Extra photos are angles of that same character, not additional replacements."}
      </Typography>
    </Stack>
  );
}

function ReferenceAngles({targetId,name,onImport,pending,cleanupEnabled,cleanupEndpoint,references,ids,primary,count,onRemove,onPrimary,onReuse}: {
  targetId:string;name:string;onImport:(targetId:string,files:File[])=>void;pending:boolean;
  cleanupEnabled:boolean;cleanupEndpoint?:string;references:ReferenceAsset[];ids:string[];primary?:string;count:number;
  onRemove:(referenceId:string)=>void;onPrimary:(referenceId:string)=>void;onReuse:(reference:ReferenceAsset)=>void;
}) {
  const picker=useRef<HTMLInputElement>(null);
  const available=references.filter(reference=>!ids.includes(reference.id));
  return <Stack spacing={1.5} sx={{...PANEL}}>
    <Typography fontWeight={650}>Reference angles for {name}</Typography>
    {ids.length>0 && <Box sx={{display:"grid",gridTemplateColumns:{xs:"repeat(2,minmax(0,1fr))",sm:"repeat(3,minmax(0,1fr))"},gap:1.5}}>
      {ids.map((id,index)=>{const reference=references.find(item=>item.id===id);return <Stack key={id} spacing={1} sx={{minWidth:0}}>
        {reference?<Box component="img" src={reference.url} alt={`${name} angle ${index+1}`} sx={{height:130,width:"100%",objectFit:"contain",bgcolor:"background.default"}}/>:<Typography color="error">Photo unavailable. Upload it again or remove this angle.</Typography>}
        <Typography variant="caption">{id===(primary??ids[0])?"Primary photo · outfit and appearance":`Additional angle ${index}`}</Typography>
        <Stack direction="row" flexWrap="wrap" gap={.5}>
          {id!==(primary??ids[0])&&reference&&<Button size="small" disabled={pending} onClick={()=>onPrimary(id)}>Make primary</Button>}
          <Button size="small" disabled={pending} onClick={()=>onRemove(id)} aria-label={`Remove angle ${index+1} for ${name}`}>Remove</Button>
        </Stack>
        {cleanupEnabled&&reference&&index>0&&<ReferenceBackground targetId={targetId} reference={reference} cleanupEndpoint={cleanupEndpoint}/>}
      </Stack>;})}
    </Box>}
    <Button variant="outlined" disabled={pending||count>=MAX_REFERENCE_IMAGES} onClick={()=>picker.current?.click()}>Add photos for {name}</Button>
    <input ref={picker} type="file" multiple hidden disabled={pending||count>=MAX_REFERENCE_IMAGES} accept="image/jpeg,image/png,image/webp,image/mpo,.mpo" aria-label={`Add reference angles for ${name}`} onChange={event=>{
      const files=Array.from(event.target.files??[]);if(files.length&&!pending)onImport(targetId,files);event.target.value="";
    }}/>
    {available.length>0&&<FormControl size="small"><InputLabel id={`reuse-${targetId}`}>Reuse uploaded photo</InputLabel>
      <Select labelId={`reuse-${targetId}`} label="Reuse uploaded photo" value="" disabled={pending} onChange={event=>{const reference=references.find(item=>item.id===event.target.value);if(reference)onReuse(reference);}}>
        {available.map(reference=><MenuItem key={reference.id} value={reference.id}>{reference.name}</MenuItem>)}
      </Select></FormControl>}
    <Typography variant="caption" color="text.secondary">Up to six photos total across your render. Every angle in this card must show the same replacement character.</Typography>
  </Stack>;
}
