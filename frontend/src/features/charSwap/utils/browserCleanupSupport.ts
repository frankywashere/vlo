// WebKit can terminate the whole tab while loading this graph, before a worker
// error reaches JavaScript. Avoid starting it on iOS, including desktop-mode iPads
// and third-party iOS browsers. API presence alone did not prevent that failure.
export function isIOSCleanupDevice(userAgent: string, platform: string, touchPoints: number): boolean {
  return /iPad|iPhone|iPod/i.test(userAgent) || (platform === "MacIntel" && touchPoints > 1);
}
