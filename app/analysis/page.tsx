import { PolyCopyShell } from "../components/PolyCopyShell";
import LegacyAnalysis from "./legacy";
import Link from "next/link";

export default function AnalysisPage() {
  return (
    <PolyCopyShell
      active="analysis"
      title="钱包分析"
      subtitle="研究公开持仓、仓位变化与共同持仓，不干扰实盘控制台"
      actions={<Link className="pcButton primary" href="/">返回总览</Link>}
    >
      <div className="pcAnalysisLegacy">
        <LegacyAnalysis />
      </div>
    </PolyCopyShell>
  );
}
