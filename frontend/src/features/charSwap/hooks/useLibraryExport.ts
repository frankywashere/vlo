import { useEffect, useRef, useState } from "react";
import { loadAccountJobs } from "../services/accounts";
import { libraryExport } from "../utils/libraryExport";

export function useLibraryExport() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  const pending = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  async function download() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const jobs = await loadAccountJobs();
      if (!active.current) return;
      const url = URL.createObjectURL(
        new Blob([libraryExport(jobs)], { type: "application/json" }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = "recastfx-library.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      globalThis.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      if (active.current)
        setError(
          "Your library details could not be downloaded. Please try again.",
        );
    } finally {
      pending.current = false;
      if (active.current) setBusy(false);
    }
  }
  return { busy, error, download };
}
