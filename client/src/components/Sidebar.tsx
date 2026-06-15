import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { useSidebar } from "../lib/sidebarContext";
import type { IconProps } from "./icons";
import {
  IconBag,
  IconCaretDoubleLeft,
  IconCaretDoubleRight,
  IconCoin,
  IconHouse,
  IconMoon,
  IconPie,
  IconSliders,
  IconSun,
} from "./icons";

type NavItem = { to: string; label: string; Icon: (props: IconProps) => JSX.Element };
const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Overview", Icon: IconHouse },
  { to: "/spending", label: "Spending", Icon: IconBag },
  { to: "/income", label: "Income", Icon: IconCoin },
  { to: "/budget", label: "Budget", Icon: IconPie },
  { to: "/rules", label: "Rules", Icon: IconSliders },
];

function readCollapsed(): boolean {
  return localStorage.getItem("sidebarCollapsed") !== "0";
}

function readDark(): boolean {
  return document.documentElement.getAttribute("data-theme") === "dark";
}

function applyTheme(dark: boolean): void {
  if (dark) document.documentElement.setAttribute("data-theme", "dark");
  else document.documentElement.removeAttribute("data-theme");
}

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [isDark, setIsDark] = useState(readDark);
  const { open } = useSidebar();

  useEffect(() => {
    localStorage.setItem("sidebarCollapsed", collapsed ? "1" : "0");
  }, [collapsed]);

  // Mirror theme across tabs and follow live OS preference when the user
  // hasn't set an explicit choice.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key !== "theme") return;
      const dark = e.newValue === "dark";
      applyTheme(dark);
      setIsDark(dark);
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    function onMedia(ev: MediaQueryListEvent) {
      if (localStorage.getItem("theme") !== null) return;
      applyTheme(ev.matches);
      setIsDark(ev.matches);
    }
    window.addEventListener("storage", onStorage);
    mq.addEventListener?.("change", onMedia);
    return () => {
      window.removeEventListener("storage", onStorage);
      mq.removeEventListener?.("change", onMedia);
    };
  }, []);

  function toggleTheme() {
    const next = !isDark;
    setIsDark(next);
    applyTheme(next);
    localStorage.setItem("theme", next ? "dark" : "light");
  }

  return (
    <aside className={`sidebar${collapsed ? " collapsed" : ""}${open ? " open" : ""}`} id="sidebar">
      <button
        type="button"
        className="sidebar-collapse-toggle"
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((c) => !c)}
      >
        <span className="sidebar-collapse-icon-expand">
          <IconCaretDoubleRight />
        </span>
        <span className="sidebar-collapse-icon-collapse">
          <IconCaretDoubleLeft />
        </span>
      </button>
      <NavLink to="/" className="logo" aria-label="Nelly's Wallet home">
        <img className="logo-img" src="/favicon.svg" alt="" />
      </NavLink>
      <nav>
        {NAV_ITEMS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            data-tooltip={label}
            className={({ isActive }) => (isActive ? "active" : undefined)}
          >
            <Icon className="nav-icon" />
            <span className="nav-label">{label}</span>
          </NavLink>
        ))}
      </nav>
      <button
        className="theme-toggle"
        type="button"
        aria-label="Toggle dark mode"
        onClick={toggleTheme}
      >
        <IconMoon />
        <IconSun />
        <span className="label-dark">Dark mode</span>
        <span className="label-light">Light mode</span>
      </button>
    </aside>
  );
}
