import { downloaderRequest } from "./videoDownloader";
export interface YouTubeSession { configured: boolean; expired: boolean; account_label: string; saved_at: number }
export interface YouTubeSessionUpdate { cookies?: string; account_label?: string; disconnect?: boolean; dedicated_account_confirmed?: boolean }
export async function youtubeSession(update?: YouTubeSessionUpdate): Promise<YouTubeSession> {
  return (await downloaderRequest("/api/commerce/admin/youtube-session", update)).json() as Promise<YouTubeSession>;
}
export async function youtubeCookieFile(file: File): Promise<string> {
  if (file.size > 60 * 1024) throw new Error("Export only YouTube cookies, in a file under 60 KB.");
  const text = await file.text();
  const lines = text.replace(/\r\n/g, "\n").split("\n").filter(line => line.trim() && (!line.startsWith("#") || line.startsWith("#HttpOnly_")));
  if (!lines.length || lines.some(line => {
    const fields = line.replace(/^#HttpOnly_/, "").split("\t");
    const domain = fields[0].replace(/^\./, "").toLowerCase();
    return fields.length !== 7 || (domain !== "youtube.com" && !domain.endsWith(".youtube.com"));
  })) throw new Error("This must be a YouTube-only Netscape cookie export. Cookies from other websites are not sent.");
  return text;
}
