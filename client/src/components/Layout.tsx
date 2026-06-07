import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";

export function Layout() {
  return (
    <div className="layout">
      <div className="sidebar-backdrop" id="sidebar-backdrop" aria-hidden />
      <Sidebar />
      <main>
        <Outlet />
      </main>
    </div>
  );
}
