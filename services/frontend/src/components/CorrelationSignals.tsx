"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Activity,
  TrendingUp,
  TrendingDown,
  Zap,
  BarChart3,
  ChevronDown,
  ChevronUp,
  X,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { getCorrelationSignals, getMarketData } from "@/lib/api-client";
import type { CorrelationSignalResponse, MarketDataResponse } from "@/lib/types";

interface CorrelationSignalsProps {
  topicId: string;
  onSymbolClick?: (symbol: string) => void;
}

const SIGNAL_TYPE_CONFIG: Record<
  string,
  { color: string; bg: string; icon: React.ComponentType<{ className?: string }> }
> = {
  sentiment_price_divergence: {
    color: "text-yellow-400",
    bg: "bg-yellow-400/10",
    icon: Activity,
  },
  sentiment_spike: {
    color: "text-purple-400",
    bg: "bg-purple-400/10",
    icon: Zap,
  },
  volume_anomaly: {
    color: "text-cyan-400",
    bg: "bg-cyan-400/10",
    icon: BarChart3,
  },
  momentum_shift: {
    color: "text-emerald-400",
    bg: "bg-emerald-400/10",
    icon: TrendingUp,
  },
  correlation_break: {
    color: "text-red-400",
    bg: "bg-red-400/10",
    icon: TrendingDown,
  },
};

function getSignalConfig(signalType: string) {
  return (
    SIGNAL_TYPE_CONFIG[signalType] || {
      color: "text-gray-400",
      bg: "bg-gray-400/10",
      icon: Activity,
    }
  );
}

function getStrengthColor(strength: number | null): string {
  if (strength == null) return "#6B7280";
  if (strength >= 0.8) return "#EF4444";
  if (strength >= 0.6) return "#F59E0B";
  if (strength >= 0.4) return "#3B82F6";
  return "#6B7280";
}

function formatSignalType(type: string): string {
  return type
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function CorrelationSignals({ topicId, onSymbolClick }: CorrelationSignalsProps) {
  const [signals, setSignals] = useState<CorrelationSignalResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expandedMarketData, setExpandedMarketData] = useState<MarketDataResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    setLoading(true);
    getCorrelationSignals(topicId)
      .then(setSignals)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [topicId]);

  const handleExpand = useCallback(async (signal: CorrelationSignalResponse) => {
    if (expandedId === signal.id) {
      setExpandedId(null);
      setExpandedMarketData(null);
      return;
    }

    setExpandedId(signal.id);
    setExpandedMarketData(null);
    setLoadingDetail(true);
    try {
      const md = await getMarketData(signal.symbol);
      setExpandedMarketData(md);
    } catch {
      // No market data available
    } finally {
      setLoadingDetail(false);
    }
  }, [expandedId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (signals.length === 0) {
    return (
      <div className="card p-8 flex flex-col items-center justify-center text-center">
        <Activity className="w-10 h-10 text-gray-600 mb-3" />
        <p className="text-gray-400 text-sm">No correlation signals detected</p>
        <p className="text-gray-600 text-xs mt-1">
          Signals appear when sentiment patterns correlate with price movements
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
        Correlation Signals
      </h3>

      {/* Timeline */}
      <div className="relative">
        {/* Timeline line */}
        <div className="absolute left-5 top-0 bottom-0 w-px bg-surface-border" />

        <div className="space-y-0">
          {signals.map((signal, idx) => {
            const config = getSignalConfig(signal.signal_type);
            const SignalIcon = config.icon;
            const isExpanded = expandedId === signal.id;
            const strengthColor = getStrengthColor(signal.signal_strength);

            return (
              <div key={signal.id} className="relative pl-12">
                {/* Timeline dot */}
                <div
                  className={`absolute left-3 top-4 w-4 h-4 rounded-full border-2 border-surface-raised flex items-center justify-center`}
                  style={{ backgroundColor: strengthColor }}
                >
                  <div className="w-1.5 h-1.5 bg-surface-raised rounded-full" />
                </div>

                <button
                  onClick={() => handleExpand(signal)}
                  className={`w-full text-left card p-4 mb-2 hover:bg-surface-overlay transition-colors ${
                    isExpanded ? "ring-1 ring-accent/30" : ""
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-3 flex-1 min-w-0">
                      <div className={`p-1.5 rounded ${config.bg} shrink-0`}>
                        <SignalIcon className={`w-3.5 h-3.5 ${config.color}`} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-mono font-semibold text-gray-200">
                            {signal.symbol}
                          </span>
                          <span className={`text-xs px-1.5 py-0.5 rounded ${config.bg} ${config.color}`}>
                            {formatSignalType(signal.signal_type)}
                          </span>
                        </div>
                        {signal.description && (
                          <p className="text-sm text-gray-400 mt-1 line-clamp-2">
                            {signal.description}
                          </p>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-3 shrink-0">
                      {/* Strength indicator */}
                      {signal.signal_strength != null && (
                        <div className="flex items-center gap-1.5">
                          <div className="flex gap-0.5">
                            {[0.2, 0.4, 0.6, 0.8, 1.0].map((threshold) => (
                              <div
                                key={threshold}
                                className="w-1.5 h-3 rounded-sm"
                                style={{
                                  backgroundColor:
                                    (signal.signal_strength ?? 0) >= threshold
                                      ? strengthColor
                                      : "#2a2d3e",
                                }}
                              />
                            ))}
                          </div>
                          <span className="text-xs font-mono text-gray-500">
                            {((signal.signal_strength ?? 0) * 100).toFixed(0)}%
                          </span>
                        </div>
                      )}

                      <span className="text-xs text-gray-500 whitespace-nowrap">
                        {formatDistanceToNow(new Date(signal.detected_at), { addSuffix: true })}
                      </span>

                      {isExpanded ? (
                        <ChevronUp className="w-4 h-4 text-gray-500" />
                      ) : (
                        <ChevronDown className="w-4 h-4 text-gray-500" />
                      )}
                    </div>
                  </div>
                </button>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="card p-4 mb-2 ml-0 border-l-2 border-accent/30 space-y-3">
                    {loadingDetail ? (
                      <div className="flex items-center justify-center h-16">
                        <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                      </div>
                    ) : (
                      <>
                        {expandedMarketData && (
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            <div>
                              <p className="text-xs text-gray-500">Price</p>
                              <p className="text-sm font-mono text-gray-200">
                                ${expandedMarketData.price?.toLocaleString(undefined, { minimumFractionDigits: 2 }) ?? "—"}
                              </p>
                            </div>
                            <div>
                              <p className="text-xs text-gray-500">Change</p>
                              <p
                                className="text-sm font-mono"
                                style={{
                                  color: (expandedMarketData.price_change_pct ?? 0) >= 0
                                    ? "#10B981"
                                    : "#EF4444",
                                }}
                              >
                                {(expandedMarketData.price_change_pct ?? 0) >= 0 ? "+" : ""}
                                {(expandedMarketData.price_change_pct ?? 0).toFixed(2)}%
                              </p>
                            </div>
                            <div>
                              <p className="text-xs text-gray-500">Volume</p>
                              <p className="text-sm font-mono text-gray-200">
                                {expandedMarketData.volume?.toLocaleString() ?? "—"}
                              </p>
                            </div>
                            <div>
                              <p className="text-xs text-gray-500">Data Source</p>
                              <p className="text-sm text-gray-200">
                                {expandedMarketData.data_source ?? "—"}
                              </p>
                            </div>
                          </div>
                        )}

                        <div className="flex items-center gap-2 pt-2 border-t border-surface-border">
                          {onSymbolClick && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                onSymbolClick(signal.symbol);
                              }}
                              className="text-xs text-accent hover:text-accent-hover transition-colors"
                            >
                              View full detail &rarr;
                            </button>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
