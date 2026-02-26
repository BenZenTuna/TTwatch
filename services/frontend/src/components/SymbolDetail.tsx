"use client";

import { useEffect, useState, useCallback } from "react";
import {
  X,
  TrendingUp,
  TrendingDown,
  Volume2,
  DollarSign,
  BarChart3,
  ArrowUpDown,
  Newspaper,
  ExternalLink,
} from "lucide-react";
import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Line,
} from "recharts";
import { formatDistanceToNow, format } from "date-fns";
import {
  getMarketData,
  getPriceHistory,
  getTopicArticles,
  getInvestmentAnalyses,
} from "@/lib/api-client";
import type {
  MarketDataResponse,
  PriceHistoryResponse,
  ArticleResponse,
  InvestmentAnalysisResponse,
} from "@/lib/types";
import { AnalysisCard } from "./AnalysisCard";

interface SymbolDetailProps {
  symbol: string;
  topicId: string;
  onClose: () => void;
}

function formatLargeNumber(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1e12) return (n / 1e12).toFixed(2) + "T";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return n.toLocaleString();
}

type ChartRange = "30" | "90" | "180" | "365";

export function SymbolDetail({ symbol, topicId, onClose }: SymbolDetailProps) {
  const [marketData, setMarketData] = useState<MarketDataResponse | null>(null);
  const [priceHistory, setPriceHistory] = useState<PriceHistoryResponse[]>([]);
  const [articles, setArticles] = useState<ArticleResponse[]>([]);
  const [analyses, setAnalyses] = useState<InvestmentAnalysisResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [chartRange, setChartRange] = useState<ChartRange>("90");

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [md, ph, allAnalyses] = await Promise.all([
        getMarketData(symbol).catch(() => null),
        getPriceHistory(symbol, parseInt(chartRange)).catch(() => []),
        getInvestmentAnalyses(topicId).catch(() => []),
      ]);
      setMarketData(md);
      setPriceHistory(ph);
      setAnalyses(allAnalyses.filter((a) => a.symbol === symbol));

      // Load articles that may mention this symbol
      getTopicArticles(topicId, { limit: 10 })
        .then(setArticles)
        .catch(() => {});
    } finally {
      setLoading(false);
    }
  }, [symbol, topicId, chartRange]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Close on Escape
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  // Chart data: reverse so oldest first
  const chartData = [...priceHistory].reverse().map((p) => ({
    date: format(new Date(p.trade_date), "MMM d"),
    rawDate: p.trade_date,
    close: p.close,
    high: p.high,
    low: p.low,
    open: p.open,
    volume: p.volume,
  }));

  const priceChange = marketData?.price_change_pct ?? 0;
  const isPositive = priceChange >= 0;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed top-0 right-0 h-full w-full max-w-3xl bg-surface-raised border-l border-surface-border z-50 overflow-y-auto animate-slide-in">
        {/* Header */}
        <div className="sticky top-0 bg-surface-raised border-b border-surface-border px-6 py-4 flex items-center justify-between z-10">
          <div className="flex items-center gap-3">
            <span className="text-xl font-bold font-mono text-gray-100">
              {symbol}
            </span>
            {marketData && (
              <span className="text-sm text-gray-500">
                {marketData.asset_type}
              </span>
            )}
            {marketData?.is_stale && (
              <span className="text-xs text-yellow-500 bg-yellow-500/10 px-2 py-0.5 rounded">
                Stale
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-64">
            <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <div className="p-6 space-y-6">
            {/* Price card */}
            {marketData && (
              <div className="card p-5">
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-3xl font-bold text-gray-100 font-mono">
                      ${marketData.price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? "—"}
                    </p>
                    <div className={`flex items-center gap-1 mt-1 text-sm font-medium ${isPositive ? "text-emerald-400" : "text-red-400"}`}>
                      {isPositive ? (
                        <TrendingUp className="w-4 h-4" />
                      ) : (
                        <TrendingDown className="w-4 h-4" />
                      )}
                      {isPositive ? "+" : ""}
                      {priceChange.toFixed(2)}%
                    </div>
                  </div>
                  <span className="text-xs text-gray-500">
                    {formatDistanceToNow(new Date(marketData.fetched_at), { addSuffix: true })}
                  </span>
                </div>

                {/* Metrics grid */}
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mt-5 pt-4 border-t border-surface-border">
                  <MetricItem
                    icon={Volume2}
                    label="Volume"
                    value={formatLargeNumber(marketData.volume)}
                  />
                  <MetricItem
                    icon={DollarSign}
                    label="Market Cap"
                    value={formatLargeNumber(marketData.market_cap)}
                  />
                  <MetricItem
                    icon={BarChart3}
                    label="P/E Ratio"
                    value={marketData.pe_ratio?.toFixed(2) ?? "—"}
                  />
                  <MetricItem
                    icon={ArrowUpDown}
                    label="52W High"
                    value={marketData.fifty_two_week_high != null ? `$${marketData.fifty_two_week_high.toLocaleString()}` : "—"}
                  />
                  <MetricItem
                    icon={ArrowUpDown}
                    label="52W Low"
                    value={marketData.fifty_two_week_low != null ? `$${marketData.fifty_two_week_low.toLocaleString()}` : "—"}
                  />
                  <MetricItem
                    icon={BarChart3}
                    label="Beta"
                    value={marketData.beta?.toFixed(2) ?? "—"}
                  />
                </div>
              </div>
            )}

            {/* Price chart */}
            {chartData.length > 0 && (
              <div className="card p-5">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
                    Price History
                  </h3>
                  <div className="flex bg-surface border border-surface-border rounded-md p-0.5">
                    {(["30", "90", "180", "365"] as ChartRange[]).map((range) => (
                      <button
                        key={range}
                        onClick={() => setChartRange(range)}
                        className={`px-2.5 py-1 text-xs rounded transition-colors ${
                          chartRange === range
                            ? "bg-accent text-white"
                            : "text-gray-500 hover:text-gray-300"
                        }`}
                      >
                        {range === "365" ? "1Y" : `${range}D`}
                      </button>
                    ))}
                  </div>
                </div>

                <ResponsiveContainer width="100%" height={300}>
                  <ComposedChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: "#6B7280", fontSize: 11 }}
                      tickLine={false}
                      axisLine={{ stroke: "#2a2d3e" }}
                      interval="preserveStartEnd"
                    />
                    <YAxis
                      yAxisId="price"
                      domain={["auto", "auto"]}
                      tick={{ fill: "#6B7280", fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                      tickFormatter={(v: number) => `$${v}`}
                    />
                    <YAxis
                      yAxisId="volume"
                      orientation="right"
                      tick={false}
                      axisLine={false}
                      tickLine={false}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "#161923",
                        border: "1px solid #2a2d3e",
                        borderRadius: "8px",
                        fontSize: "12px",
                      }}
                      labelStyle={{ color: "#9CA3AF" }}
                      itemStyle={{ color: "#E5E7EB" }}
                    />
                    <Area
                      yAxisId="price"
                      type="monotone"
                      dataKey="close"
                      stroke="#3B82F6"
                      fill="#3B82F620"
                      strokeWidth={2}
                      name="Close"
                    />
                    <Line
                      yAxisId="price"
                      type="monotone"
                      dataKey="high"
                      stroke="#10B98140"
                      strokeWidth={1}
                      dot={false}
                      name="High"
                    />
                    <Line
                      yAxisId="price"
                      type="monotone"
                      dataKey="low"
                      stroke="#EF444440"
                      strokeWidth={1}
                      dot={false}
                      name="Low"
                    />
                    <Bar
                      yAxisId="volume"
                      dataKey="volume"
                      fill="#3B82F615"
                      name="Volume"
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Latest analysis */}
            {analyses.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
                  Latest Analysis
                </h3>
                <AnalysisCard analysis={analyses[0]} />
              </div>
            )}

            {/* Related articles */}
            {articles.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                  <Newspaper className="w-3.5 h-3.5" />
                  Related Articles
                </h3>
                <div className="card divide-y divide-surface-border overflow-hidden">
                  {articles.slice(0, 8).map((article) => (
                    <div
                      key={article.id}
                      className="px-4 py-3 hover:bg-surface-overlay transition-colors"
                    >
                      <a
                        href={article.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm font-medium text-gray-200 hover:text-accent transition-colors line-clamp-1 flex items-center gap-1.5"
                      >
                        {article.title}
                        <ExternalLink className="w-3 h-3 shrink-0 text-gray-600" />
                      </a>
                      <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
                        {article.source_name && <span>{article.source_name}</span>}
                        {article.published_at && (
                          <span>
                            {formatDistanceToNow(new Date(article.published_at), {
                              addSuffix: true,
                            })}
                          </span>
                        )}
                        {article.sentiment_score != null && (
                          <span
                            className="font-mono"
                            style={{
                              color:
                                article.sentiment_score > 0.1
                                  ? "#10B981"
                                  : article.sentiment_score < -0.1
                                  ? "#EF4444"
                                  : "#6B7280",
                            }}
                          >
                            {article.sentiment_score > 0 ? "+" : ""}
                            {article.sentiment_score.toFixed(2)}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}

function MetricItem({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div>
      <div className="flex items-center gap-1 text-xs text-gray-500 mb-0.5">
        <Icon className="w-3 h-3" />
        {label}
      </div>
      <p className="text-sm font-mono text-gray-200">{value}</p>
    </div>
  );
}
