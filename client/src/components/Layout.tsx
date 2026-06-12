import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { SidebarContext } from "../lib/sidebarContext";

export function Layout() {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    document.body.classList.toggle("sidebar-open", open);
    return () => document.body.classList.remove("sidebar-open");
  }, [open]);

  return (
    <SidebarContext.Provider value={{ open, setOpen }}>
      <div className="layout">
        <div
          className={`sidebar-backdrop${open ? " visible" : ""}`}
          aria-hidden
          onClick={() => setOpen(false)}
        />
        <Sidebar />
        <main>
          <Outlet />
        </main>
      </div>
    </SidebarContext.Provider>
  );
}
