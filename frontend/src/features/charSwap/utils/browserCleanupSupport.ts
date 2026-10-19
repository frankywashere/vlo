// WebKit can terminate the whole tab while loading this graph, before a worker
// error reaches JavaScript. Route iOS, including desktop-mode iPads and other
// iOS browsers, to the tested CPU runtime without requesting a GPU adapter.
export function isIOSCleanupDevice(userAgent: string, platform: string, touchPoints: number): boolean {
  return /iPad|iPhone|iPod/i.test(userAgent) || (platform === "MacIntel" && touchPoints > 1);
}
