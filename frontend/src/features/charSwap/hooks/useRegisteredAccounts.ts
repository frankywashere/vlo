import { useEffect, useState } from "react";
import { loadRegisteredAccounts } from "../services/commerce";
import type { RegisteredAccount } from "../services/commerce";

export function useRegisteredAccounts(enabled = true) {
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    setLoading(true); setError(""); setAccounts([]);
    void (async () => {
      const collected = new Map<string, RegisteredAccount>();
      const seen = new Set<string>();
      let cursor: string | undefined;
      try {
        do {
          const page = await loadRegisteredAccounts(cursor);
          if (!active) return;
          for (const account of page.accounts) collected.set(account.email, account);
          setAccounts([...collected.values()].sort((a, b) => a.email.localeCompare(b.email)));
          cursor = page.next_cursor ?? undefined;
          if (cursor && seen.has(cursor)) throw new Error("Account loading stopped. Refresh the list to try again.");
          if (cursor) seen.add(cursor);
        } while (cursor && active);
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : "Accounts could not be loaded."); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, [enabled, revision]);
  return {accounts, loading, error, refresh: () => setRevision((value) => value + 1)};
}
