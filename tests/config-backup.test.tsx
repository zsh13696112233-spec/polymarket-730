import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConfigBackupPanel from "../app/components/ConfigBackupPanel";

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("配置备份", () => {
  it("下载服务端保存的 JSON，保留金额字符串", async () => {
    const backup = { product: "PolyCopy", version: 1, settings: { price: "0.512345678901234567" }, exclusions: [] };
    vi.stubGlobal("fetch", vi.fn(async () => json(backup)));
    const create = vi.fn<(blob: Blob) => string>(() => "blob:config");
    const revoke = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toMatch(/^polycopy-config-.*\.json$/);
      expect(this.href).toBe("blob:config");
    });
    render(<ConfigBackupPanel onImported={vi.fn()} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "导出配置" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已导出保存的配置");
    expect(click).toHaveBeenCalledOnce();
    const blob = create.mock.calls[0][0] as Blob;
    const contents = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blob);
    });
    expect(JSON.parse(contents)).toEqual(backup);
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:config"));
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/whales/config/export"), expect.anything());
  });

  it("取消选择不发请求，格式错误可重新选择同一个文件", async () => {
    const imported = vi.fn();
    vi.stubGlobal("fetch", vi.fn());
    render(<ConfigBackupPanel onImported={imported} />);
    const input = screen.getByLabelText("选择配置文件");
    fireEvent.change(input, { target: { files: [] } });
    expect(fetch).not.toHaveBeenCalled();
    const user = userEvent.setup();
    const invalid = new File(["not json"], "config.json", { type: "application/json" });
    await user.upload(input, invalid);
    expect(await screen.findByRole("alert")).toHaveTextContent("不是有效的 JSON");
    expect(fetch).not.toHaveBeenCalled();
    await user.upload(input, invalid);
    expect(await screen.findByRole("alert")).toHaveTextContent("不是有效的 JSON");
    expect(imported).not.toHaveBeenCalled();
  });

  it("发送原始内容并防止重复提交，成功后通知表单刷新", async () => {
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
    const imported = vi.fn();
    render(<ConfigBackupPanel onImported={imported} />);
    const content = '{"version":1,"settings":{"amount":"5.123456789012345678"}}';
    await userEvent.setup().upload(screen.getByLabelText("选择配置文件"), new File([content], "config.json", { type: "application/json" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "导入中…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "导出配置" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "导出配置" }));
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/whales/config/import"), expect.objectContaining({ method: "POST", body: content }));
    await act(async () => finish(json({ added: 2, updated: 1, retained: 3, message: "自动跟单已关闭。" })));
    expect(await screen.findByRole("status")).toHaveTextContent("新增 2 个、更新备注 1 个、保留 3 个");
    expect(imported).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "导入配置" })).toBeEnabled();
  });

  it("展示接口校验错误，失败时不刷新表单且允许重试", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: [{ loc: ["body", "settings", "enabled"], msg: "Field required" }] }, 422)));
    const imported = vi.fn();
    render(<ConfigBackupPanel onImported={imported} />);
    await userEvent.setup().upload(screen.getByLabelText("选择配置文件"), new File(["{}"], "config.json", { type: "application/json" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("settings.enabled：Field required");
    expect(imported).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "导入配置" })).toBeEnabled();
  });
});
