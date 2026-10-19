import {useRef,useState} from "react";
import type {DragEvent} from "react";

// File-picker and drop paths share their import/validation function.
export function useFileDrop(onImport: (file: File) => void, disabled: boolean) {
  const depth = useRef(0);
  const [dragging,setDragging] = useState(false);
  const hasFiles = (event: DragEvent<HTMLElement>) => Array.from(event.dataTransfer.types ?? []).includes("Files");
  return {dragging: dragging && !disabled, dropHandlers: {
    onDragEnter(event: DragEvent<HTMLElement>) {
      if (!hasFiles(event)) return;
      event.preventDefault(); depth.current++; if (!disabled) setDragging(true);
    },
    onDragOver(event: DragEvent<HTMLElement>) {
      if (!hasFiles(event)) return;
      event.preventDefault(); event.dataTransfer.dropEffect = disabled ? "none" : "copy";
      if (!disabled) setDragging(true);
    },
    onDragLeave(event: DragEvent<HTMLElement>) {
      if (!hasFiles(event)) return;
      depth.current = Math.max(0,depth.current-1); if (!depth.current) setDragging(false);
    },
    onDrop(event: DragEvent<HTMLElement>) {
      event.preventDefault(); event.stopPropagation(); depth.current=0; setDragging(false);
      const file = event.dataTransfer.files[0];
      if (file && !disabled) onImport(file);
    },
  }};
}
