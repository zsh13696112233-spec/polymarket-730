import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RootLayout from "../app/layout";

vi.mock("next/headers", () => ({ headers: vi.fn() }));

afterEach(() => vi.unstubAllEnvs());

describe("全站信息隐藏", () => {
  it.each([undefined, "1"])("配置 %s 时不挂载业务页面", (value) => {
    vi.stubEnv("NEXT_PUBLIC_HIDE_SYSTEM_INFO", value);
    const businessPage = vi.fn(() => <div>业务数据</div>);
    const BusinessPage = businessPage;
    const layout = RootLayout({ children: <BusinessPage /> });
    render(layout.props.children.props.children);
    expect(screen.getByText("页面信息已隐藏")).toBeInTheDocument();
    expect(screen.queryByText("业务数据")).not.toBeInTheDocument();
    expect(businessPage).not.toHaveBeenCalled();
  });

  it("配置 0 时恢复业务页面", () => {
    vi.stubEnv("NEXT_PUBLIC_HIDE_SYSTEM_INFO", "0");
    const layout = RootLayout({ children: <div>业务数据</div> });
    render(layout.props.children.props.children);
    expect(screen.getByText("业务数据")).toBeInTheDocument();
    expect(screen.queryByText("页面信息已隐藏")).not.toBeInTheDocument();
  });
});
