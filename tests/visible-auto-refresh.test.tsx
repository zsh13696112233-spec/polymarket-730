import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useVisibleAutoRefresh } from "../app/components/useVisibleAutoRefresh";

function AutoRefreshHarness({ refresh, intervalMs = 1_000 }: {
  refresh: () => Promise<void> | void;
  intervalMs?: number;
}) {
  useVisibleAutoRefresh(refresh, intervalMs);
  return null;
}

function setVisibility(value: "hidden" | "visible") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
});

describe("可见页面自动刷新", () => {
  it("按间隔刷新，并在页面隐藏时暂停、恢复可见时立即刷新", async () => {
    vi.useFakeTimers();
    const refresh = vi.fn(async () => undefined);
    render(<AutoRefreshHarness refresh={refresh} />);

    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(refresh).toHaveBeenCalledTimes(1);

    act(() => setVisibility("hidden"));
    await act(() => vi.advanceTimersByTimeAsync(3_000));
    expect(refresh).toHaveBeenCalledTimes(1);

    await act(async () => setVisibility("visible"));
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("上一轮未结束时不会启动重叠刷新", async () => {
    vi.useFakeTimers();
    let finishRefresh: (() => void) | undefined;
    const refresh = vi.fn(() => new Promise<void>((resolve) => {
      finishRefresh = resolve;
    }));
    render(<AutoRefreshHarness refresh={refresh} />);

    await act(() => vi.advanceTimersByTimeAsync(3_000));
    expect(refresh).toHaveBeenCalledTimes(1);

    await act(async () => finishRefresh?.());
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
