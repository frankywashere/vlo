import { useEffect, useState } from "react";
import { loadCreditCatalog, loadCreditWallet } from "../services/commerce";
import type { CreditCatalog, CreditWallet } from "../services/commerce";
import {
  LOCAL_PREVIEW_CATALOG,
  localPreviewWallet,
} from "../utils/localPreviewCredits";

interface LiveCreditState {
  catalog: CreditCatalog | null;
  wallet: CreditWallet | null;
  error: string;
  localPreview?: boolean;
}

export function useLiveCredits(
  enabled: boolean,
  revision?: string,
  email?: string | null,
): LiveCreditState {
  const previewWallet = enabled
    ? localPreviewWallet(
        email,
        globalThis.location.hostname,
        import.meta.env.VITE_LOCAL_PREVIEW_ACCOUNT,
        import.meta.env.VITE_LOCAL_PREVIEW_CREDITS,
      )
    : null;
  const localPreview = Boolean(previewWallet);
  const [catalog, setCatalog] = useState<CreditCatalog | null>(null);
  const [wallet, setWallet] = useState<CreditWallet | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled || localPreview) return;
    let active = true;
    let latestRequest = 0;
    const reload = () => {
      const request = ++latestRequest;
      void Promise.allSettled([loadCreditCatalog(), loadCreditWallet()]).then(
        ([nextCatalog, nextWallet]) => {
          if (!active || request !== latestRequest) return;
          setCatalog(
            nextCatalog.status === "fulfilled" ? nextCatalog.value : null,
          );
          setWallet(
            nextWallet.status === "fulfilled" ? nextWallet.value : null,
          );
          setError(
            nextWallet.status === "rejected"
              ? "Your credit balance is temporarily unavailable. Try again shortly."
              : nextCatalog.status === "rejected"
                ? "Credit pricing is temporarily unavailable. Try again shortly."
                : "",
          );
        },
      );
    };
    reload();
    globalThis.addEventListener("focus", reload);
    globalThis.addEventListener("recastfx:credits-updated", reload);
    return () => {
      active = false;
      globalThis.removeEventListener("focus", reload);
      globalThis.removeEventListener("recastfx:credits-updated", reload);
    };
  }, [enabled, revision, localPreview]);
  return {
    catalog: localPreview ? LOCAL_PREVIEW_CATALOG : catalog,
    wallet: previewWallet ?? wallet,
    error: localPreview ? "" : error,
    localPreview,
  };
}
