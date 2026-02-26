"use client";

import { useState, useMemo, useCallback, useRef } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceArea,
  CartesianGrid,
} from "recharts";
import type { SentimentPointResponse } from "@/lib/types";
import { getClusterColor } from "@/lib/design-tokens";
import { format } from "date-fns";

interface SentimentTimelineProps {
  data: SentimentPointResponse[];
}

interface ChartRow {
  period: string;
  periodMs: number;
  [clusterKeyword: string]: string | number;
}

export function SentimentTimeline({ data }: SentimentTimelineProps) {
  // Discover unique cluster keywords
  const clusterKeywords = useMemo(() => {
    const set = new Set<string>();
    for (const d of data) set.add(d.cluster_keyword);
    return Array.from(set);
  }, [data]);

  // Toggle state for each series
  const [visible, setVisible] = useState<Record<string, boolean>>(() => {
    const map: Record<string, boolean> = {};
    for (const kw of clusterKeywords) map[kw] = true;
    return map;
  });

  // Zoom state
  const [refAreaLeft, setRefAreaLeft] = useState<string | null>(null);
  const [refAreaRight, setRefAreaRight] = useState<string | null>(null);
  const [zoomDomain, setZoomDomain] = useState<[string, string] | null>(null);
  const isDragging = useRef(false);

  // Pivot data: rows keyed by period_start, columns = cluster keywords
  const chartData = useMemo(() => {
    const map = new Map<string, ChartRow>();

    for (const pt of data) {
      const period = pt.period_start;
      if (!map.has(period)) {
        map.set(period, {
          period: format(new Date(period), "MMM d"),
          periodMs: new Date(period).getTime(),
        });
      }
      map.get(period)![pt.cluster_keyword] = pt.avg_sentiment;
    }

    return Array.from(map.values()).sort((a, b) => a.periodMs - b.periodMs);
  }, [data]);

  // Apply zoom filter
  const displayData = useMemo(() => {
    if (!zoomDomain) return chartData;
    return chartData.filter(
      (d) => d.period >= zoomDomain[0] && d.period <= zoomDomain[1]
    );
  }, [chartData, zoomDomain]);

  const toggleSeries = useCallback((keyword: string) => {
    setVisible((prev) => ({ ...prev, [keyword]: !prev[keyword] }));
  }, []);

  const handleMouseDown = useCallback(
    (e: { activeLabel?: string }) => {
      if (e.activeLabel) {
        isDragging.current = true;
        setRefAreaLeft(e.activeLabel);
        setRefAreaRight(null);
      }
    },
    []
  );

  const handleMouseMove = useCallback(
    (e: { activeLabel?: string }) => {
      if (isDragging.current && e.activeLabel) {
        setRefAreaRight(e.activeLabel);
      }
    },
    []
  );

  const handleMouseUp = useCallback(() => {
    if (refAreaLeft && refAreaRight && refAreaLeft !== refAreaRight) {
      const [left, right] =
        refAreaLeft < refAreaRight
          ? [refAreaLeft, refAreaRight]
          : [refAreaRight, refAreaLeft];
      setZoomDomain([left, right]);
    }
    isDragging.current = false;
    setRefAreaLeft(null);
    setRefAreaRight(null);
  }, [refAreaLeft, refAreaRight]);

  const resetZoom = useCallback(() => {
    setZoomDomain(null);
  }, []);

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500 text-sm">
        No sentiment data available
      </div>
    );
  }

  return (
    <div>
      {/* Toggle buttons */}
      <div className="flex flex-wrap gap-2 mb-3">
        {clusterKeywords.map((kw, i) => {
          const color = getClusterColor(i);
          const active = visible[kw];
          return (
            <button
              key={kw}
              onClick={() => toggleSeries(kw)}
              className="text-xs px-2 py-1 rounded border transition-colors"
              style={{
                borderColor: active ? color : "#2a2d3e",
                color: active ? color : "#6b7280",
                backgroundColor: active ? `${color}15` : "transparent",
              }}
            >
              {kw}
            </button>
          );
        })}
        {zoomDomain && (
          <button
            onClick={resetZoom}
            className="text-xs px-2 py-1 rounded border border-surface-border text-gray-400 hover:text-gray-200 transition-colors"
          >
            Reset zoom
          </button>
        )}
      </div>

      <ResponsiveContainer width="100%" height={300}>
        <LineChart
          data={displayData}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          margin={{ top: 5, right: 20, bottom: 5, left: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
          <XAxis
            dataKey="period"
            tick={{ fill: "#6b7280", fontSize: 11 }}
            axisLine={{ stroke: "#2a2d3e" }}
            tickLine={false}
          />
          <YAxis
            domain={[-1, 1]}
            tick={{ fill: "#6b7280", fontSize: 11 }}
            axisLine={{ stroke: "#2a2d3e" }}
            tickLine={false}
            tickFormatter={(v: number) => v.toFixed(1)}
          />
          <Tooltip
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              return (
                <div className="bg-surface-overlay border border-surface-border rounded-lg px-3 py-2 shadow-lg">
                  <p className="text-xs text-gray-400 mb-1">{label}</p>
                  {payload.map((p) => (
                    <p
                      key={p.name}
                      className="text-xs"
                      style={{ color: p.color }}
                    >
                      {p.name}: {(p.value as number).toFixed(3)}
                    </p>
                  ))}
                </div>
              );
            }}
          />
          <Legend
            verticalAlign="bottom"
            height={24}
            formatter={(value: string) => (
              <span className="text-xs text-gray-300">{value}</span>
            )}
          />
          {clusterKeywords.map((kw, i) =>
            visible[kw] ? (
              <Line
                key={kw}
                type="monotone"
                dataKey={kw}
                stroke={getClusterColor(i)}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0 }}
                connectNulls
              />
            ) : null
          )}
          {refAreaLeft && refAreaRight && (
            <ReferenceArea
              x1={refAreaLeft}
              x2={refAreaRight}
              strokeOpacity={0.3}
              fill="#3B82F6"
              fillOpacity={0.1}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
      <p className="text-xs text-gray-600 mt-1">
        Click and drag to zoom. Click &quot;Reset zoom&quot; to restore.
      </p>
    </div>
  );
}
