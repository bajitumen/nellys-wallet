import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError, getJson, postJson } from "../lib/api";

type PlaidStatus = { has_creds: boolean };

export default function PlaidSetupPage() {
  const nav = useNavigate();
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);

  const status = useQuery<PlaidStatus, ApiError>({
    queryKey: ["plaid-status"],
    queryFn: () => getJson<PlaidStatus>("/api/settings/plaid"),
    retry: false,
  });

  const save = useMutation({
    mutationFn: (vars: { plaid_client_id: string; plaid_secret: string }) =>
      postJson<{ ok: boolean }>("/api/settings/plaid", vars),
    onSuccess: () => nav("/"),
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="auth-page">
      <div className="plaid-setup-card">
        {status.data?.has_creds && (
          <button
            type="button"
            className="plaid-setup-back"
            onClick={() => nav("/")}
          >
            ← Back to dashboard
          </button>
        )}
        <h1 className="plaid-setup-title">Connect bank</h1>
        <p className="plaid-setup-subtitle">
          {status.data?.has_creds
            ? "Update your Plaid Trial credentials."
            : "Paste your Plaid Trial credentials below. We'll use them to read your account balances and transactions."}
        </p>
        <form
          className="plaid-setup-form"
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            save.mutate({ plaid_client_id: clientId, plaid_secret: secret });
          }}
        >
          <label className="plaid-setup-label">
            Plaid client ID
            <input
              type="text"
              autoComplete="off"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              required
            />
          </label>
          <label className="plaid-setup-label">
            Plaid secret
            <input
              type="password"
              autoComplete="off"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              required
            />
          </label>
          {error && <div className="plaid-setup-error">{error}</div>}
          <button
            type="submit"
            className="plaid-setup-submit"
            disabled={save.isPending}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </form>
        <div className="plaid-setup-help">
          Don't have these yet?{" "}
          <Link to="/settings/plaid/faq">How to set up Plaid →</Link>
        </div>
      </div>
    </div>
  );
}
