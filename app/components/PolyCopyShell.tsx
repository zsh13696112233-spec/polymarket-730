"use client";

import { ReactNode, useState } from "react";
import Link from "next/link";

export type WorkspaceView = "home" | "whales" | "auto-follow" | "positions" | "whale-records" | "email-records" | "settings";

const navigation: Array<{
  id: WorkspaceView;
  href: string;
  label: string;
}> = [
  { id: "home", href: "/", label: "首页" },
  { id: "whales", href: "/whales", label: "链上监测" },
  { id: "auto-follow", href: "/whales/auto-follow", label: "自动跟单" },
  { id: "positions", href: "/positions", label: "持仓管理" },
  { id: "whale-records", href: "/whales/records", label: "我的跟单" },
  { id: "email-records", href: "/email-records", label: "邮件记录" },
  { id: "settings", href: "/settings", label: "设置" },
];

function NavIcon({ id }: { id: WorkspaceView }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  if (id === "home") {
    return <svg {...common}><path d="m3.5 10 8.5-7 8.5 7" /><path d="M5.5 8.5V21h13V8.5" /><path d="M9.5 21v-7h5v7" /></svg>;
  }
  if (id === "positions") {
    return <svg {...common}><rect x="3.5" y="7" width="17" height="13" rx="2" /><path d="M8 7V5a1.5 1.5 0 0 1 1.5-1.5h5A1.5 1.5 0 0 1 16 5v2" /><path d="M3.5 12a22 22 0 0 0 17 0" /><path d="M10 13h4v3h-4z" /></svg>;
  }
  if (id === "whale-records") {
    return <svg {...common}><path d="M8.5 6h11" /><path d="M8.5 12h11" /><path d="M8.5 18h11" /><path d="m3.75 6 1 1 1.75-2" /><path d="m3.75 12 1 1 1.75-2" /><path d="m3.75 18 1 1 1.75-2" /></svg>;
  }
  if (id === "auto-follow") {
    return <svg {...common}><path d="M8 7h8" /><path d="m14 4 3 3-3 3" /><path d="M16 17H8" /><path d="m10 14-3 3 3 3" /><circle cx="12" cy="12" r="9" /></svg>;
  }
  if (id === "whales") {
    return <svg {...common}><path d="M3.5 12h3l1.8-4.25 3.1 8.5 2.25-5.25 1.35 3h5.5" /><circle cx="12" cy="12" r="9" /></svg>;
  }
  if (id === "email-records") {
    return <svg {...common}><rect x="3.5" y="5.5" width="17" height="13" rx="1.5" /><path d="m4.5 7 7.5 6 7.5-6" /></svg>;
  }
  return <svg {...common}><path d="M4 7h10" /><circle cx="17" cy="7" r="2" /><path d="M20 17H10" /><circle cx="7" cy="17" r="2" /><path d="M4 12h4" /><circle cx="11" cy="12" r="2" /><path d="M14 12h6" /></svg>;
}

export function PolyCopyShell({
  active,
  title,
  actions,
  children,
}: {
  active: WorkspaceView;
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="pcRoot">
      <button
        className={`pcScrim ${menuOpen ? "open" : ""}`}
        aria-label="关闭导航"
        type="button"
        onClick={() => setMenuOpen(false)}
      />
      <aside className={`pcSidebar ${menuOpen ? "open" : ""}`}>
        <Link className="pcBrand" href="/" aria-label="PolyCopy 首页">
          <img
            className="pcBrandMark"
            src="/icon.svg"
            alt="PolyCopy 标志"
          />
          <span>
            <strong>PolyCopy</strong>
          </span>
        </Link>
        <nav className="pcNavigation" aria-label="主导航">
          <span className="pcNavLabel">工作台</span>
          {navigation.filter((item) => item.id !== "settings").map((item) => (
            <Link
              key={item.id}
              className={active === item.id ? "active" : ""}
              href={item.href}
            >
              <span className="pcNavIcon" aria-hidden="true">
                <NavIcon id={item.id} />
              </span>
              {item.label}
            </Link>
          ))}
          <span className="pcNavLabel pcNavLabelSecondary">系统</span>
          {navigation.filter((item) => item.id === "settings").map((item) => (
            <Link
              key={item.id}
              className={active === item.id ? "active" : ""}
              href={item.href}
            >
              <span className="pcNavIcon" aria-hidden="true">
                <NavIcon id={item.id} />
              </span>
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="pcSidebarStatus">
          <span className="pcLiveDot" />
          <span>
            <strong>本机服务已连接</strong>
            <small>密钥仅存于系统钥匙串</small>
          </span>
        </div>
      </aside>
      <div className={`pcMain${active === "home" ? " homeWorkspace" : ""}${active === "whales" ? " whaleWorkspace" : ""}${active === "auto-follow" ? " whaleAutoWorkspace" : ""}`}>
        <header className="pcTopbar">
          <div className="pcTitleGroup">
            <button
              className="pcMenuButton"
              type="button"
              aria-label="打开导航"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
            </button>
            <div>
              <h1>{title}</h1>
            </div>
          </div>
          {actions && <div className="pcTopActions">{actions}</div>}
        </header>
        <main className="pcContent">{children}</main>
      </div>
    </div>
  );
}
