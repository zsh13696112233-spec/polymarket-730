"use client";

import { useEffect, useRef, useState } from "react";
import { PolyCopyShell } from "./PolyCopyShell";
import { whaleApi } from "./WhaleShared";

const DEFAULT_STATE = `[FICTIONAL SIMULATION]
Match: Harbor FC vs Mountain City FC, soccer friendly.
Market: Harbor FC to win in 90 minutes plus stoppage time. Yes pays $1 for a win, $0 otherwise.
Score: Harbor FC leads 3-0 at minute 84, second half. Updated 5 seconds ago.
Cards and stoppage time: unknown.
Position: 100 Yes shares, average entry $0.50, total cost $50. No previous sales.
Current executable sell price: $0.90, enough depth for all shares.
Fees and slippage: assumed zero for this simulation.`;

const ACTIONS = {
  SELL_ALL: "全部止盈",
  RECOVER_PRINCIPAL: "收回本金",
  HOLD: "暂缓止盈",
  ABSTAIN: "信息不足，暂不建议",
} as const;
type Action = keyof typeof ACTIONS;
type SimulationResult = {
  model: string;
  answer: { type: "choice"; choice: Action; confidence: number; probabilities: Record<Action, number> };
  elapsed_ms: number;
  raw_response: Record<string, unknown>;
};

export default function JevSimulationWorkspace() {
  const [apiKey, setApiKey] = useState("");
  const [state, setState] = useState(DEFAULT_STATE);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [submittedState, setSubmittedState] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  const submit = async () => {
    if (controller.current) return;
    const key = apiKey.trim();
    if (!key || !state.trim()) { setError("请填写 API Key 和比赛信息。"); return; }
    const request = new AbortController();
    controller.current = request;
    setLoading(true); setResult(null); setError(null); setSubmittedState(state);
    setApiKey("");
    const timer = window.setTimeout(() => request.abort(), 30_000);
    try {
      setResult(await whaleApi<SimulationResult>("/api/ai/jev/simulate", {
        method: "POST", headers: { "X-Typesafe-Api-Key": key },
        body: JSON.stringify({ state }), signal: request.signal, cache: "no-store",
      }));
    } catch (failure) {
      if (request.signal.aborted) setError("请求已取消或超时，请重新输入密钥后重试。");
      else setError(failure instanceof Error ? failure.message : "模拟评估失败，请重试。");
    } finally {
      window.clearTimeout(timer); controller.current = null; setLoading(false);
    }
  };

  return <PolyCopyShell active="ai-simulation" title="AI 止盈模拟">
    <section className="pcPanel jevSimulation" aria-label="Jev 止盈模拟">
      <header className="pcPanelHeader"><h2>Jev 决策试验</h2><span className="pcBadge muted">仅模拟 · jev-1.13.0</span></header>
      <p>手动提交会真实调用 TypeSafe，并消耗该账户额度。只评估下方示例，不读取钱包、不下单、不修改止盈设置。</p>
      <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <label htmlFor="jev-key">TypeSafe API Key</label>
        <input id="jev-key" type="password" autoComplete="off" spellCheck={false} maxLength={512}
          placeholder="输入本次调用的 API Key" value={apiKey} disabled={loading} required
          onChange={(event) => setApiKey(event.target.value)} />
        <small>密钥仅用于本次请求，提交后清空，不写入数据库或浏览器存储。比赛信息会发送给 TypeSafe。</small>
        <label htmlFor="jev-state">比赛与持仓信息（可编辑）</label>
        <textarea id="jev-state" rows={12} maxLength={16000} required value={state} disabled={loading}
          onChange={(event) => { setState(event.target.value); setResult(null); setError(null); }} />
        <small>默认示例和模型判断指令使用英文；手动编辑内容将原样发送，不自动翻译。示例采用零费用假设。本页不自动抓取赛况或计算交易方案。</small>
        <div className="jevActions">
          <button className="pcButton primary" type="submit" disabled={loading || !apiKey.trim() || !state.trim()}>
            {loading ? "正在评估…" : "模拟评估"}
          </button>
          <button className="pcButton ghost" type="button" disabled={loading}
            onClick={() => { setState(DEFAULT_STATE); setResult(null); setError(null); }}>恢复默认示例</button>
        </div>
      </form>
      {loading && <p role="status">正在等待 Jev 返回，本次不会执行交易。</p>}
      {error && <div className="pcFormError" role="alert">{error}</div>}
    </section>
    {result && <section className="pcPanel jevSimulation" aria-label="模拟结果">
      <header className="pcPanelHeader"><h2>建议：{ACTIONS[result.answer.choice]}</h2></header>
      <p>模型：{result.model} · 接口耗时：{(result.elapsed_ms / 1000).toFixed(2)} 秒 · 置信度：{(result.answer.confidence * 100).toFixed(1)}%</p>
      <p>置信度和下方概率表示模型对选项的判断，不是比赛胜率，也不是收益保证。此接口不提供自由文本理由。</p>
      <div className="pcTableWrap"><table className="positionsTable"><thead><tr><th>选项</th><th>模型选项概率</th></tr></thead>
        <tbody>{Object.entries(ACTIONS).map(([action, label]) => <tr key={action}><td>{label}</td>
          <td>{(result.answer.probabilities[action as Action] * 100).toFixed(1)}%</td></tr>)}</tbody></table></div>
      <details><summary>本次提交的比赛信息</summary><pre>{submittedState}</pre></details>
      <details><summary>原始返回（密钥已脱敏）</summary><pre>{JSON.stringify(result.raw_response, null, 2)}</pre></details>
    </section>}
  </PolyCopyShell>;
}
