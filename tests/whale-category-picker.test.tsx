import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { WhaleCategoryPicker, categorySummary } from "../app/components/WhaleCategoryPicker";
import { WhaleCategorySelection } from "../app/components/WhaleShared";

function Harness({ initial }: { initial: WhaleCategorySelection[] }) {
  const [value, setValue] = useState(initial);
  return <><WhaleCategoryPicker legend="分类" value={value} onChange={setValue} disabled={false} /><output>{JSON.stringify(value)}</output></>;
}

describe("体育和电竞细分类", () => {
  it("从整个大类取消一个项目，保留其余项目并显示部分选中", async () => {
    const user = userEvent.setup();
    render(<Harness initial={["sports", "counter-strike-2"]} />);
    await user.click(screen.getByRole("checkbox", { name: "足球" }));
    expect(screen.getByRole("checkbox", { name: "足球" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "棒球" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "传统体育" })).toBePartiallyChecked();
    expect(screen.getByRole("checkbox", { name: "CS2" })).toBeChecked();
    expect(screen.getByRole("status").textContent).not.toContain('"sports"');
    await user.click(screen.getByRole("checkbox", { name: "传统体育" }));
    expect(screen.getByRole("checkbox", { name: "足球" })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "传统体育" }));
    expect(screen.getByRole("status")).toHaveTextContent('["counter-strike-2"]');
  });

  it("细分类回显、逐项全选和取消全选", async () => {
    const user = userEvent.setup();
    render(<Harness initial={["dota-2"]} />);
    expect(screen.getByRole("checkbox", { name: "电竞" })).toBePartiallyChecked();
    for (const name of ["CS2", "英雄联盟（LOL）", "其他电竞"]) {
      await user.click(screen.getByRole("checkbox", { name }));
    }
    expect(screen.getByRole("status")).toHaveTextContent('["esports"]');
    expect(screen.getByRole("checkbox", { name: "电竞" })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "电竞" }));
    expect(screen.getByRole("status")).toHaveTextContent('[]');
    expect(categorySummary(["soccer", "counter-strike-2", "league-of-legends"])).toBe("足球、CS2、英雄联盟（LOL）");
  });
});
