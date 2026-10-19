import { useEffect, useRef, useState } from "react";

export function useVideoDownload() {
  const request = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => () => request.current?.abort(), []);
  async function download(source: string) {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(source, { signal: controller.signal });
      if (!response.ok) throw new Error("Download failed");
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      // A file MIME type avoids handing MP4 blobs to the mobile video player.
      const file = new Blob([blob], { type: "application/octet-stream" });
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = "recastfx-video.mp4";
      document.body.appendChild(link);
      link.click();
      link.remove();
      globalThis.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      if (!controller.signal.aborted)
        setError("The download failed. Refresh the video link and try again.");
    } finally {
      request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return { busy, error, download };
}
