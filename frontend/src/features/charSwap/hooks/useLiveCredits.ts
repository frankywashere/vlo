import { useEffect, useState } from "react";
import { loadCreditCatalog, loadCreditWallet } from "../services/commerce";
import type { CreditCatalog, CreditWallet } from "../services/commerce";

export function useLiveCredits(enabled: boolean, revision?: string) {
  const [catalog, setCatalog] = useState<CreditCatalog | null>(null);
  const [wallet, setWallet] = useState<CreditWallet | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    const reload = () => {
      void Promise.all([loadCreditCatalog(), loadCreditWallet()]).then(([nextCatalog, nextWallet]) => {
        if (active) {setCatalog(nextCatalog); setWallet(nextWallet); setError("");}
      }).catch(() => {if (active) setError("Your credit balance could not be checked. Refresh before submitting.");});
    };
    reload();
    globalThis.addEventListener("focus", reload);
    globalThis.addEventListener("recastfx:credits-updated", reload);
    return () => {active = false; globalThis.removeEventListener("focus", reload); globalThis.removeEventListener("recastfx:credits-updated", reload);};
  }, [enabled, revision]);
  return {catalog, wallet, error};
}
