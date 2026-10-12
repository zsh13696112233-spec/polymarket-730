"use client";

import { WhaleCategorySelection } from "./WhaleShared";

export const AUTO_CATEGORIES: Array<{ key: WhaleCategorySelection; label: string }> = [
  { key: "sports", label: "传统体育" },
  { key: "esports", label: "电竞" },
  { key: "politics", label: "政治" },
  { key: "crypto", label: "加密" },
  { key: "science_tech", label: "科学与科技" },
  { key: "entertainment", label: "娱乐" },
  { key: "other", label: "其他" },
];

export const SUBCATEGORIES: Partial<Record<WhaleCategorySelection, Array<{ key: WhaleCategorySelection; label: string }>>> = {
  esports: [
    { key: "dota-2", label: "Dota 2" },
    { key: "counter-strike-2", label: "CS2" },
    { key: "league-of-legends", label: "英雄联盟（LOL）" },
    { key: "other-esports", label: "其他电竞" },
  ],
  sports: [
    { key: "soccer", label: "足球" },
    { key: "tennis", label: "网球" },
    { key: "basketball", label: "篮球" },
    { key: "baseball", label: "棒球" },
    { key: "football", label: "美式橄榄球" },
    { key: "hockey", label: "冰球" },
    { key: "mma", label: "综合格斗" },
    { key: "boxing", label: "拳击" },
    { key: "cricket", label: "板球" },
    { key: "golf", label: "高尔夫" },
    { key: "motorsports", label: "赛车" },
    { key: "other-sports", label: "其他体育" },
  ],
};

export function categorySummary(categories: WhaleCategorySelection[]): string {
  const options = [...AUTO_CATEGORIES, ...Object.values(SUBCATEGORIES).flat()];
  return categories.map((key) => options.find((item) => item.key === key)?.label || key).join("、");
}

export function WhaleCategoryPicker({ value, onChange, disabled, legend }: {
  value: WhaleCategorySelection[];
  onChange: (value: WhaleCategorySelection[]) => void;
  disabled: boolean;
  legend: string;
}) {
  return (
    <fieldset className="whaleAutoCategories whaleCategoryPicker" disabled={disabled}>
      <legend>{legend}</legend>
      {AUTO_CATEGORIES.map((parent) => {
        const children = SUBCATEGORIES[parent.key] ?? [];
        const childKeys = children.map((child) => child.key);
        const all = value.includes(parent.key) || (children.length > 0 && childKeys.every((key) => value.includes(key)));
        const partial = !all && childKeys.some((key) => value.includes(key));
        const outside = value.filter((key) => key !== parent.key && !childKeys.includes(key));
        return (
          <div className="whaleCategoryGroup" key={parent.key}>
            <label>
              <input type="checkbox" checked={all} ref={(element) => { if (element) element.indeterminate = partial; }}
                onChange={() => onChange(all ? outside : [...outside, parent.key])} />
              <span>{parent.label}</span>
            </label>
            {children.length > 0 && <div className="whaleCategoryChildren">
              {children.map((child) => (
                <label key={child.key}>
                  <input type="checkbox" checked={all || value.includes(child.key)} onChange={() => {
                    const selected = all ? childKeys : childKeys.filter((key) => value.includes(key));
                    const next = selected.includes(child.key) ? selected.filter((key) => key !== child.key) : [...selected, child.key];
                    onChange([...outside, ...(next.length === childKeys.length ? [parent.key] : next)]);
                  }} />
                  <span>{child.label}</span>
                </label>
              ))}
            </div>}
          </div>
        );
      })}
    </fieldset>
  );
}
