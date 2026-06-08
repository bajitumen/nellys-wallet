import type { ReactNode } from "react";
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SignedIn, UserButton } from "@clerk/clerk-react";
import { IconRefresh } from "./icons";
import { AddAccountButton } from "./AddAccountButton";
import { useToast } from "./Toast";
import { getJson, postJson } from "../lib/api";
import { useClerkEnabled } from "../lib/clerkContext";
import { useSidebar } from "../lib/sidebarContext";

type Props = {
  heading: ReactNode;
  children: ReactNode;
};

export function Page({ heading, children }: Props) {
  const qc = useQueryClient();
  const toast = useToast();
  const clerkEnabled = useClerkEnabled();
  const { open, setOpen } = useSidebar();
  const me = useQuery<{ last_sync_label: string | null }>({
    queryKey: ["me"],
    queryFn: () => getJson("/api/me"),
    retry: false,
    staleTime: 60_000,
  });
  const sync = useMutation({
    mutationFn: () => postJson<{ ok: boolean }>("/sync"),
    onSuccess: () => qc.invalidateQueries(),
    onError: (e: Error) => toast.error(`Sync failed: ${e.message}`),
  });

  useEffect(() => {
    if (typeof heading === "string") document.title = `${heading} · Nelly's Wallet`;
  }, [heading]);

  return (
    <>
      <div className="page-header">
        <button
          className="sidebar-toggle"
          type="button"
          aria-label="Open menu"
          aria-controls="sidebar"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          <svg
            className="icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            aria-hidden
          >
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <h1>{heading}</h1>
        <div className="page-actions">
          <button
            className="action-btn"
            type="button"
            aria-label="Refresh data"
            title={me.data?.last_sync_label ? `Last synced ${me.data.last_sync_label}` : "Refresh"}
            disabled={sync.isPending}
            onClick={() => sync.mutate()}
          >
            <IconRefresh />
          </button>
          <AddAccountButton />
          {clerkEnabled && (
            <SignedIn>
              <div className="header-user-button">
                <UserButton />
              </div>
            </SignedIn>
          )}
        </div>
      </div>
      {children}
    </>
  );
}
