import { createContext, useContext } from "react";

export const ClerkEnabledContext = createContext(false);

export function useClerkEnabled(): boolean {
  return useContext(ClerkEnabledContext);
}
