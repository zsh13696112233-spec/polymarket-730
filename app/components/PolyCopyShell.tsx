"use client";

import { ReactNode, useState } from "react";
import Link from "next/link";

export type WorkspaceView =
  | "overview"
  | "positions"
  | "records"
  | "analysis"
  | "settings";

const navigation: Array<{
  id: WorkspaceView;
  href: string;
  label: string;
  icon: string;
}> = [
  { id: "overview", href: "/", label: "总览", icon: "⌂" },
  { id: "positions", href: "/positions", label: "持仓", icon: "◇" },
  { id: "records", href: "/records", label: "跟单记录", icon: "≡" },
  { id: "analysis", href: "/analysis", label: "钱包分析", icon: "⌁" },
  { id: "settings", href: "/settings", label: "设置", icon: "⚙" },
];

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
          <span className="pcBrandMark">P</span>
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
                {item.icon}
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
                {item.icon}
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
      <div className="pcMain">
        <header className="pcTopbar">
          <div className="pcTitleGroup">
            <button
              className="pcMenuButton"
              type="button"
              aria-label="打开导航"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              ☰
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
