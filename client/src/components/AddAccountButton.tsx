import { useCallback, useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { usePlaidLink, type PlaidLinkOnSuccess } from "react-plaid-link";
import { postJson } from "../lib/api";
import { IconPlus } from "./icons";

export function AddAccountButton() {
  const qc = useQueryClient();
  const [linkToken, setLinkToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const fetchToken = useMutation({
    mutationFn: () => postJson<{ link_token: string }>("/link/token"),
    onSuccess: (data) => setLinkToken(data.link_token),
    onError: (e: Error) => {
      setBusy(false);
      alert(`Could not start Plaid Link: ${e.message}`);
    },
  });

  const exchange = useMutation({
    mutationFn: (public_token: string) =>
      postJson<{ ok: boolean }>("/link/exchange", { public_token }),
    onSuccess: async () => {
      await postJson("/sync").catch(() => {});
      qc.invalidateQueries();
      setBusy(false);
    },
    onError: (e: Error) => {
      setBusy(false);
      alert(`Could not link account: ${e.message}`);
    },
  });

  const onSuccess = useCallback<PlaidLinkOnSuccess>((public_token) => {
    exchange.mutate(public_token);
  }, [exchange]);

  const { open, ready } = usePlaidLink({
    token: linkToken,
    onSuccess,
    onExit: () => {
      setLinkToken(null);
      setBusy(false);
    },
  });

  useEffect(() => {
    if (linkToken && ready) open();
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
