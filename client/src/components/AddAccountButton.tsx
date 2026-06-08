import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { usePlaidLink, type PlaidLinkOnSuccess } from "react-plaid-link";
import { postJson } from "../lib/api";
import { IconPlus } from "./icons";
import { useToast } from "./Toast";

export function AddAccountButton() {
  const qc = useQueryClient();
  const toast = useToast();
  const [linkToken, setLinkToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const openedRef = useRef(false);

  const fetchToken = useMutation({
    mutationFn: () => postJson<{ link_token: string }>("/link/token"),
    onSuccess: (data) => setLinkToken(data.link_token),
    onError: (e: Error) => {
      setBusy(false);
      toast.error(`Could not start Plaid Link: ${e.message}`);
    },
  });

  const exchange = useMutation({
    mutationFn: (public_token: string) =>
      postJson<{ ok: boolean }>("/link/exchange", { public_token }),
    onSuccess: async () => {
      await postJson("/sync").catch(() => {});
      qc.invalidateQueries({ queryKey: ["overview"] });
      qc.invalidateQueries({ queryKey: ["spending"] });
      qc.invalidateQueries({ queryKey: ["income"] });
      qc.invalidateQueries({ queryKey: ["budget"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: ["plaid-status"] });
      setBusy(false);
    },
    onError: (e: Error) => {
      setBusy(false);
      toast.error(`Could not link account: ${e.message}`);
    },
  });

  // exchange.mutate is the stable handle for the mutation. The mutation object
  // itself rerenders, so depending on it would re-init Plaid Link constantly.
  const exchangeMutate = exchange.mutate;
  const onSuccess = useCallback<PlaidLinkOnSuccess>(
    (public_token) => exchangeMutate(public_token),
    [exchangeMutate],
  );

  const { open, ready } = usePlaidLink({
    token: linkToken,
    onSuccess,
    onExit: () => {
      openedRef.current = false;
      setLinkToken(null);
      setBusy(false);
    },
  });

  useEffect(() => {
    if (linkToken && ready && !openedRef.current) {
      openedRef.current = true;
      open();
    }
  }, [linkToken, ready, open]);

  return (
    <button
      className="action-btn"
      type="button"
      aria-label="Add account"
      title="Add account"
      disabled={busy}
      onClick={() => {
        setBusy(true);
        fetchToken.mutate();
      }}
    >
      <IconPlus />
    </button>
  );
}
