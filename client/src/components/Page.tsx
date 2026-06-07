import type { ReactNode } from "react";
import { useEffect } from "react";
import { UserButton, useUser } from "@clerk/clerk-react";
import { IconPlus, IconRefresh } from "./icons";

type Props = {
  heading: ReactNode;
  children: ReactNode;
  onRefresh?: () => void;
  lastSyncedLabel?: string;
  onAddAccount?: () => void;
};

export function Page({ heading, children, onRefresh, lastSyncedLabel, onAddAccount }: Props) {
  const { isSignedIn } = useUser();

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
          aria-expanded={false}
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
        {isSignedIn && (
          <div className="page-actions">
            {onRefresh && (
              <button
                className="action-btn"
                type="button"
                aria-label="Refresh data"
                title={lastSyncedLabel ? `Last synced ${lastSyncedLabel}` : "Refresh"}
                onClick={onRefresh}
              >
                <IconRefresh />
              </button>
            )}
            {onAddAccount && (
              <button
                className="action-btn"
                type="button"
                aria-label="Add account"
                title="Add account"
                onClick={onAddAccount}
              >
                <IconPlus />
              </button>
            )}
            <div className="header-user-button">
              <UserButton />
            </div>
          </div>
        )}
      </div>
      {children}
    </>
  );
}
