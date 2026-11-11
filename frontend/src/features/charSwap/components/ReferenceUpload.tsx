import type { RefObject } from "react";
import { Button } from "@mui/material";
import UploadRounded from "@mui/icons-material/UploadRounded";
import type { TargetPerson } from "../model/model";

interface ReferenceUploadProps {
  person: TargetPerson;
  pending: boolean;
  hasReference: boolean;
  onImport: (id: string, file: File) => void;
  onImportAdditional?: (id: string, files: File[]) => void;
  atLimit?: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
}
export function ReferenceUpload({
  person,
  pending,
  hasReference,
  onImport,
  onImportAdditional,
  atLimit = false,
  inputRef,
}: ReferenceUploadProps) {
  const disabled = pending || (!!onImportAdditional && atLimit);
  const label = hasReference
    ? onImportAdditional ? "Add photos" : "Change image"
    : "Upload reference";
  function openPicker() {
    if (!disabled) inputRef.current?.click();
  }
  return (
    <>
      <Button
        variant={hasReference ? "outlined" : "contained"}
        startIcon={<UploadRounded />}
        disabled={disabled}
        aria-label={`${label} for ${person.name}`}
        onClick={openPicker}
        sx={{ minHeight: 44 }}
      >
        {pending
          ? "Opening image…"
          : label}
      </Button>
      <input
        ref={inputRef}
        type="file"
        hidden
        disabled={disabled}
        multiple={!!onImportAdditional}
        accept="image/jpeg,image/png,image/webp,image/mpo,.mpo"
        aria-label={`Upload reference for ${person.name}`}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length && !disabled) {
            if (onImportAdditional) onImportAdditional(person.id, files);
            else onImport(person.id, files[0]);
          }
          event.target.value = "";
        }}
      />
    </>
  );
}
