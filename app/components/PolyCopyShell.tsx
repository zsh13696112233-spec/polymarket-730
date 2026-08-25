"use client";

import { ReactNode, useState } from "react";
import Link from "next/link";

export type WorkspaceView =
  | "overview"
  | "positions"
  | "records"
  | "whales"
  | "settings";

const navigation: Array<{
  id: WorkspaceView;
  href: string;
  label: string;
}> = [
  { id: "overview", href: "/", label: "总览" },
  { id: "positions", href: "/positions", label: "持仓" },
  { id: "records", href: "/records", label: "记录" },
  { id: "whales", href: "/whales", label: "链上监测" },
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

  if (id === "overview") {
    return <svg {...common}><rect x="3.75" y="3.75" width="6.5" height="6.5" rx="1.25" /><rect x="13.75" y="3.75" width="6.5" height="6.5" rx="1.25" /><rect x="3.75" y="13.75" width="6.5" height="6.5" rx="1.25" /><rect x="13.75" y="13.75" width="6.5" height="6.5" rx="1.25" /></svg>;
  }
  if (id === "positions") {
    return <svg {...common}><path d="M4 8.25h16v10.5H4z" /><path d="M8.25 8.25V6.5A1.5 1.5 0 0 1 9.75 5h4.5a1.5 1.5 0 0 1 1.5 1.5v1.75" /><path d="M4 12.25h16" /></svg>;
  }
  if (id === "records") {
    return <svg {...common}><path d="M8.5 6h11" /><path d="M8.5 12h11" /><path d="M8.5 18h11" /><path d="m3.75 6 1 1 1.75-2" /><path d="m3.75 12 1 1 1.75-2" /><path d="m3.75 18 1 1 1.75-2" /></svg>;
  }
  if (id === "whales") {
    return <svg {...common}><path d="M3.5 12h3l1.8-4.25 3.1 8.5 2.25-5.25 1.35 3h5.5" /><circle cx="12" cy="12" r="9" /></svg>;
  }
  return <svg {...common}><path d="M4 7h10" /><circle cx="17" cy="7" r="2" /><path d="M20 17H10" /><circle cx="7" cy="17" r="2" /><path d="M4 12h4" /><circle cx="11" cy="12" r="2" /><path d="M14 12h6" /></svg>;
}

export function PolyCopyShell({
  active,
  title,
  subtitle,
  actions,
  children,
}: {
  active: WorkspaceView;
  title: string;
  subtitle: string;
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
            <small>POLYMARKET COPY TRADING</small>
          </span>
        </Link>
        <nav className="pcNavigation" aria-label="主导航">
          <span className="pcNavLabel">工作台</span>
          {navigation.slice(0, 4).map((item) => (
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
          {navigation.slice(4).map((item) => (
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
      <div className={`pcMain${active === "whales" ? " whaleWorkspace" : ""}`}>
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
              <p>{subtitle}</p>
            </div>
          </div>
          {actions && <div className="pcTopActions">{actions}</div>}
        </header>
        <main className="pcContent">{children}</main>
      </div>
    </div>
  );
}
