const PREVIEW_HOST = "recastfx-polished-preview.web.app";

/** Keep the auth helper first-party on the approved connected preview site. */
export function getAuthDomain(hostname: string): string {
  return hostname === PREVIEW_HOST ? PREVIEW_HOST : "recastfx.com";
}
