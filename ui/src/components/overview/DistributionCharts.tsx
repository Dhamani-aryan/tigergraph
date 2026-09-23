import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { OverviewStats } from "@/data/diagnostics";
import { Panel } from "@/components/ui/primitives";
import { PieChart as PieIcon } from "lucide-react";

const VERDICT_COLOR: Record<string, string> = { fraud: "#ef4444", legitimate: "#10b981", uncertain: "#fbbf24" };

export function VerdictChart({ stats }: { stats: OverviewStats }) {
  const order = ["fraud", "uncertain", "legitimate"];
  const keys = [...order.filter((k) => k in stats.verdicts), ...Object.keys(stats.verdicts).filter((k) => !order.includes(k))];
  const data = keys.map((k) => ({ name: k, count: stats.verdicts[k] ?? 0 }));
  const patterns = Object.entries(stats.patterns).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...patterns.map(([, n]) => n));
  return (
    <Panel title="Verdict distribution" icon={<PieIcon className="h-3.5 w-3.5" aria-hidden />} bodyClassName="p-4">
      <figure aria-label={`Verdicts: ${data.map((d) => `${d.count} ${d.name}`).join(", ")}`}>
        <div className="h-36">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 8 }}>
              <XAxis type="number" allowDecimals={false} stroke="#52525b" fontSize={11} tickLine={false} />
              <YAxis type="category" dataKey="name" stroke="#a1a1aa" fontSize={12} width={78} tickLine={false} axisLine={false} />
              <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} contentStyle={{ background: "#10151D", border: "1px solid #2A3441", borderRadius: 8, fontSize: 12 }} />
              <Bar dataKey="count" radius={[0, 4, 4, 0]} isAnimationActive={false} label={{ position: "right", fill: "#e4e4e7", fontSize: 12 }}>
                {data.map((d) => (
                  <Cell key={d.name} fill={VERDICT_COLOR[d.name] ?? "#71717a"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </figure>
      <h3 className="mb-2 mt-4 text-[11px] font-medium uppercase tracking-wider text-zinc-500">Patterns</h3>
      <ul className="space-y-1.5">
        {patterns.map(([p, n]) => (
          <li key={p} className="grid grid-cols-[1fr_auto] items-center gap-2 text-[12.5px]">
            <div className="min-w-0">
              <div className="flex justify-between gap-2">
                <span className={p === "undocumented" ? "font-mono text-violet-300" : "font-mono text-zinc-300"}>{p}</span>
              </div>
              <div className="mt-0.5 h-1 rounded-full bg-ink-700">
                <div className={p === "undocumented" ? "h-full rounded-full bg-violet-400" : "h-full rounded-full bg-tg-500/80"} style={{ width: `${(n / max) * 100}%` }} />
              </div>
            </div>
            <span className="font-mono tabular-nums text-zinc-200">{n}</span>
          </li>
        ))}
        {patterns.length === 0 && <li className="text-sm text-zinc-500">No patterns reported.</li>}
      </ul>
    </Panel>
  );
}
