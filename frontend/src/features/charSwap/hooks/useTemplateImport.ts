import { useEffect, useState } from "react";
import { loadTemplate, loadTemplateDraft } from "../services/templates";
import type { TemplateDraft } from "../services/templates";
import { creditsForDuration } from "../utils/creditPricing";
import type { CreditCatalog } from "../services/commerce";

interface TemplateImportOptions {
  id: string | null;
  enabled: boolean;
  blocked: boolean;
  balance?: number;
  catalog: CreditCatalog | null;
  creditError: string;
  onReady: (draft: TemplateDraft) => void;
}
export function useTemplateImport({
  id,
  enabled,
  blocked,
  balance,
  catalog,
  creditError,
  onReady,
}: TemplateImportOptions) {
  const [error, setError] = useState<{
    id: string;
    message: string;
    credits?: number;
  } | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!id || !enabled || (balance === undefined && !creditError)) return;
    const controller = new AbortController();
    let needed: number | undefined;
    async function open() {
      if (blocked)
        throw new Error(
          "Wait for your current upload or submission before choosing another template.",
        );
      if (creditError) throw new Error(creditError);
      const option = await loadTemplate(id!, controller.signal);
      needed = creditsForDuration(option.anchor ? option.duration ?? 4 : 4, catalog);
      if (balance === undefined || balance < needed)
        throw new Error(
          `This template needs ${needed} credits. Add credits to continue.`,
        );
      const draft = await loadTemplateDraft(option, controller.signal);
      needed = creditsForDuration(draft.target ? draft.trim[1] - draft.trim[0] : 4, catalog);
      if (balance < needed) {
        URL.revokeObjectURL(draft.clip.url);
        throw new Error(
          `This clip needs ${needed} credits. Your template is saved while you top up.`,
        );
      }
      try {
        controller.signal.throwIfAborted();
        onReady(draft);
      } catch (cause) {
        URL.revokeObjectURL(draft.clip.url);
        throw cause;
      }
    }
    void open().catch((cause) => {
      if (!controller.signal.aborted)
        setError({
          id,
          message:
            cause instanceof Error
              ? cause.message
              : "The template could not be opened.",
          credits: needed,
        });
    });
    return () => controller.abort();
  }, [id, enabled, blocked, balance, catalog, creditError, retry, onReady]);
  return {
    error: error?.id === id ? error.message : null,
    needsCredits:
      error?.id === id &&
      error.credits !== undefined &&
      (balance ?? 0) < error.credits,
    retry: () => {
      setError(null);
      setRetry((value) => value + 1);
    },
  };
}
