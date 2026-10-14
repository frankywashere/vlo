import { useRef } from "react";
import { Button } from "@mui/material";
import UploadRounded from "@mui/icons-material/UploadRounded";
import type { TargetPerson } from "../model/model";

interface ReferenceUploadProps {
  person: TargetPerson;
  pending: boolean;
  hasReference: boolean;
  onImport: (id: string, file: File) => void;
}
export function ReferenceUpload({
  person,
  pending,
  hasReference,
  onImport,
}: ReferenceUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <Button
        variant={hasReference ? "outlined" : "contained"}
        startIcon={<UploadRounded />}
        disabled={pending}
        onClick={() => inputRef.current?.click()}
        sx={{ minHeight: 44 }}
      >
        {pending
          ? "Opening image…"
          : hasReference
            ? "Change image"
            : "Upload reference"}
      </Button>
      <input
        ref={inputRef}
        type="file"
        hidden
        disabled={pending}
        accept="image/jpeg,image/png,image/webp"
        aria-label={`Upload reference for ${person.name}`}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onImport(person.id, file);
          event.target.value = "";
        }}
      />
    </>
  );
}
