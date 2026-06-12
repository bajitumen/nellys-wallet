import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ClerkProvider } from "@clerk/clerk-react";

import App from "./App";
import { ClerkEnabledContext } from "./lib/clerkContext";
import { ToastProvider } from "./components/Toast";
import "./index.css";

const clerkKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: true, staleTime: 30_000 },
  },
});

function Root() {
  const enabled = !!clerkKey;
  const tree = (
    <ClerkEnabledContext.Provider value={enabled}>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <BrowserRouter>
            <App clerkEnabled={enabled} />
          </BrowserRouter>
        </ToastProvider>
      </QueryClientProvider>
    </ClerkEnabledContext.Provider>
  );
  if (!enabled) return tree;
  try {
    return <ClerkProvider publishableKey={clerkKey!}>{tree}</ClerkProvider>;
  } catch (err) {
    console.error("Clerk failed to initialize", err);
    return (
      <div style={{ padding: "2rem", color: "#fff", background: "#0f1011", minHeight: "100vh" }}>
        <h1>Clerk failed to load</h1>
        <p>Check that VITE_CLERK_PUBLISHABLE_KEY is set correctly in client/.env.local.</p>
        <pre>{String(err)}</pre>
      </div>
    );
  }
}

createRoot(document.getElementById("root")!).render(<Root />);
