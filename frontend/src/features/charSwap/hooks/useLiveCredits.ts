import { useEffect, useState } from "react";
import { loadCreditCatalog, loadCreditWallet } from "../services/commerce";
import type { CreditCatalog, CreditWallet } from "../services/commerce";
import { isLongRenderPreviewHost, loadLongRenderCapabilities } from "../longRender/api";
import {
  LOCAL_PREVIEW_CATALOG,
  localPreviewWallet,
} from "../utils/localPreviewCredits";

interface LiveCreditState {
  catalog: CreditCatalog | null;
  wallet: CreditWallet | null;
  error: string;
  localPreview?: boolean;
  isolatedPreview?: boolean;
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
  const isolatedPreview = isLongRenderPreviewHost();
  const [catalog, setCatalog] = useState<CreditCatalog | null>(null);
  const [wallet, setWallet] = useState<CreditWallet | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled || localPreview) return;
    let active = true;
    let latestRequest = 0;
    const reload = () => {
      const request = ++latestRequest;
      if (isolatedPreview) {
        void loadLongRenderCapabilities().then(capability => {
          if (!active || request !== latestRequest) return;
          setWallet(capability.wallet ?? { balance: capability.test_credits, transactions: [] });
          setCatalog(capability.credit_catalog ?? null);
          setError(capability.credit_catalog ? "" : "Test credit pricing is unavailable. Refresh before generating.");
        }).catch(() => {
          if (!active || request !== latestRequest) return;
          setWallet(null); setCatalog(null);
          setError("Your test credit balance is unavailable. Refresh before generating.");
        });
        return;
      }
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
    globalThis.addEventListener("recastfx:long-render-settings", reload);
    return () => {
      active = false;
      globalThis.removeEventListener("focus", reload);
      globalThis.removeEventListener("recastfx:credits-updated", reload);
      globalThis.removeEventListener("recastfx:long-render-settings", reload);
    };
  }, [enabled, revision, localPreview, isolatedPreview]);
  return {
    catalog: localPreview ? LOCAL_PREVIEW_CATALOG : catalog,
    wallet: previewWallet ?? wallet,
    error: localPreview ? "" : error,
    localPreview,
    isolatedPreview,
  };
}
