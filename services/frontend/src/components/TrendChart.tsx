"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ResponsiveContainer,
} from "recharts";
import type { ClusterResponse } from "@/lib/types";
import { getClusterColor, VELOCITY_COLORS } from "@/lib/design-tokens";

interface TrendChartProps {
  clusters: ClusterResponse[];
  maxItems?: number;
  onClusterClick?: (cluster: ClusterResponse) => void;
}

const VELOCITY_ARROWS: Record<string, string> = {
  surging: "↑↑",
  rising: "↑",
  stable: "→",
  declining: "↓",
};

interface ChartDatum {
  keyword: string;
  trend_score: number;
  color: string;
  velocity: string | null;
  cluster: ClusterResponse;
}

export function TrendChart({
  clusters,
  maxItems = 12,
  onClusterClick,
}: TrendChartProps) {
  const sorted = [...clusters]
    .sort((a, b) => b.trend_score - a.trend_score)
    .slice(0, maxItems);

  const data: ChartDatum[] = sorted.map((c, i) => ({
    keyword: c.keyword,
    trend_score: c.trend_score,
    color: c.color || getClusterColor(i),
    velocity: c.velocity,
    cluster: c,
  }));

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500 text-sm">
        No cluster data available
      </div>
    );
  }

  const barHeight = 32;
  const chartHeight = Math.max(200, data.length * barHeight + 40);

  return (
    <ResponsiveContainer width="100%" height={chartHeight}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 40, bottom: 4, left: 0 }}
        barCategoryGap="20%"
      >
        <XAxis
          type="number"
          tick={{ fill: "#6b7280", fontSize: 11 }}
          axisLine={{ stroke: "#2a2d3e" }}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="keyword"
          tick={{ fill: "#d1d5db", fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={120}
        />
        <Tooltip
          content={({ active, payload }) => {
            if (!active || !payload?.[0]) return null;
            const d = payload[0].payload as ChartDatum;
            return (
              <div className="bg-surface-overlay border border-surface-border rounded-lg px-3 py-2 shadow-lg">
                <p className="text-sm font-medium text-gray-100">
                  {d.keyword}
                </p>
                <p className="text-xs text-gray-400 mt-1">
                  Trend score: {d.trend_score.toFixed(2)}
                </p>
                {d.velocity && (
                  <p className="text-xs mt-0.5" style={{ color: VELOCITY_COLORS[d.velocity] || "#6B7280" }}>
                    {VELOCITY_ARROWS[d.velocity] || ""} {d.velocity}
                  </p>
                )}
              </div>
            );
          }}
          cursor={{ fill: "rgba(255,255,255,0.03)" }}
        />
        <Bar
          dataKey="trend_score"
          radius={[0, 4, 4, 0]}
          onClick={(_data: ChartDatum) => {
            onClusterClick?.(_data.cluster);
          }}
          style={{ cursor: onClusterClick ? "pointer" : "default" }}
        >
          {data.map((d, i) => (
            <Cell key={i} fill={d.color} fillOpacity={0.8} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function VelocityBadge({ velocity }: { velocity: string | null }) {
  if (!velocity) return null;
  const color = VELOCITY_COLORS[velocity] || "#6B7280";
  const arrow = VELOCITY_ARROWS[velocity] || "";

  return (
    <span
      className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded"
      style={{ color, backgroundColor: `${color}15` }}
    >
      {arrow} {velocity}
    </span>
  );
}
