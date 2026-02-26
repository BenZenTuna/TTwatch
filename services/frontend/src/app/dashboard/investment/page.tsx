"use client";

import { useEffect, useState, useCallback } from "react";
import {
  TrendingUp,
  BarChart3,
  Activity,
  Bell,
  Link2,
  Plus,
  Trash2,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { useAppStore } from "@/lib/store";
import {
  getWatchlist,
  addWatchlistItem,
  removeWatchlistItem,
  getMarketData,
  getInvestmentAnalyses,
} from "@/lib/api-client";
import type {
  WatchlistItemResponse,
  WatchlistItemCreate,
  MarketDataResponse,
  InvestmentAnalysisResponse,
} from "@/lib/types";
import { AnalysisCard } from "@/components/AnalysisCard";
import { SymbolDetail } from "@/components/SymbolDetail";
import { PriceAlerts } from "@/components/PriceAlerts";
import { CorrelationSignals } from "@/components/CorrelationSignals";
import { AssetMappings } from "@/components/AssetMappings";

type InvestmentTab = "watchlist" | "analyses" | "signals" | "alerts" | "mappings";

const TABS: {
  id: InvestmentTab;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}[] = [
  { id: "watchlist", label: "Watchlist", icon: TrendingUp },
  { id: "analyses", label: "Analyses", icon: BarChart3 },
  { id: "signals", label: "Signals", icon: Activity },
  { id: "alerts", label: "Alerts", icon: Bell },
  { id: "mappings", label: "Mappings", icon: Link2 },
];

export default function InvestmentPage() {
  const { topics, selectedTopicId, selectTopic } = useAppStore();
  const [activeTab, setActiveTab] = useState<InvestmentTab>("watchlist");
  const selectedTopic = topics.find((t) => t.id === selectedTopicId);

  // Auto-select first topic if none selected
  useEffect(() => {
    if (!selectedTopicId && topics.length > 0) {
      selectTopic(topics[0].id);
    }
  }, [topics, selectedTopicId, selectTopic]);

  // Symbol detail panel
  const [detailSymbol, setDetailSymbol] = useState<string | null>(null);

  if (topics.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] text-center">
        <TrendingUp className="w-12 h-12 text-gray-600 mb-4" />
        <h2 className="text-xl font-semibold text-gray-300 mb-2">
          No topics yet
        </h2>
        <p className="text-gray-500 max-w-md">
          Create an intelligence topic to start tracking investment signals.
        </p>
      </div>
    );
  }

  if (!selectedTopicId) return null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">Investment</h1>
          {selectedTopic && (
            <p className="text-sm text-gray-500 mt-1">
              {selectedTopic.icon} {selectedTopic.name}
            </p>
          )}
        </div>

        {/* Topic selector */}
        {topics.length > 1 && (
          <div className="flex bg-surface-raised border border-surface-border rounded-lg p-1">
            {topics.map((topic) => (
              <button
                key={topic.id}
                onClick={() => selectTopic(topic.id)}
                className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                  selectedTopicId === topic.id
                    ? "bg-accent text-white"
                    : "text-gray-400 hover:text-gray-200"
                }`}
              >
                {topic.icon && <span className="mr-1.5">{topic.icon}</span>}
                {topic.name}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Sub-tab bar */}
      <div className="flex bg-surface-raised border border-surface-border rounded-lg p-1">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm transition-colors ${
                activeTab === tab.id
                  ? "bg-accent text-white"
                  : "text-gray-400 hover:text-gray-200"
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {activeTab === "watchlist" && (
        <WatchlistTab
          topicId={selectedTopicId}
          onSymbolClick={setDetailSymbol}
        />
      )}

      {activeTab === "analyses" && (
        <AnalysesTab topicId={selectedTopicId} />
      )}

      {activeTab === "signals" && (
        <CorrelationSignals
          topicId={selectedTopicId}
          onSymbolClick={setDetailSymbol}
        />
      )}

      {activeTab === "alerts" && <PriceAlerts />}

      {activeTab === "mappings" && (
        <AssetMappings
          topicId={selectedTopicId}
          onSymbolClick={setDetailSymbol}
        />
      )}

      {/* Symbol detail panel */}
      {detailSymbol && (
        <SymbolDetail
          symbol={detailSymbol}
          topicId={selectedTopicId}
          onClose={() => setDetailSymbol(null)}
        />
      )}
    </div>
  );
}

// ─── Watchlist Tab ────────────────────────────────────────────────────────────

function WatchlistTab({
  topicId,
  onSymbolClick,
}: {
  topicId: string;
  onSymbolClick: (symbol: string) => void;
}) {
  const [items, setItems] = useState<WatchlistItemResponse[]>([]);
  const [marketDataMap, setMarketDataMap] = useState<Record<string, MarketDataResponse>>({});
  const [loading, setLoading] = useState(true);
  const [showAddForm, setShowAddForm] = useState(false);

  // Add form state
  const [formSymbol, setFormSymbol] = useState("");
  const [formAssetType, setFormAssetType] = useState("stock");
  const [formNotes, setFormNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const loadWatchlist = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getWatchlist(topicId);
      setItems(data);

      // Fetch market data for each symbol in parallel
      const mdResults = await Promise.allSettled(
        data.map((item) => getMarketData(item.symbol))
      );
      const mdMap: Record<string, MarketDataResponse> = {};
      mdResults.forEach((result, idx) => {
        if (result.status === "fulfilled") {
          mdMap[data[idx].symbol] = result.value;
        }
      });
      setMarketDataMap(mdMap);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [topicId]);

  useEffect(() => {
    loadWatchlist();
  }, [loadWatchlist]);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!formSymbol.trim()) return;

    setSubmitting(true);
    try {
      await addWatchlistItem(topicId, {
        symbol: formSymbol.trim().toUpperCase(),
        asset_type: formAssetType,
        notes: formNotes || null,
      });
      setFormSymbol("");
      setFormNotes("");
      setShowAddForm(false);
      await loadWatchlist();
    } catch {
      // silent
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRemove(itemId: string) {
    try {
      await removeWatchlistItem(itemId);
      setItems((prev) => prev.filter((i) => i.id !== itemId));
    } catch {
      // silent
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header with add button */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Watchlist
        </h3>
        <button
          onClick={() => setShowAddForm(!showAddForm)}
          className="btn-primary text-sm flex items-center gap-1.5 py-1.5 px-3"
        >
          <Plus className="w-3.5 h-3.5" />
          Add Symbol
        </button>
      </div>

      {/* Add form */}
      {showAddForm && (
        <form onSubmit={handleAdd} className="card p-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Symbol</label>
              <input
                type="text"
                value={formSymbol}
                onChange={(e) => setFormSymbol(e.target.value)}
                placeholder="AAPL"
                className="input-field text-sm"
                required
              />
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Asset Type</label>
              <select
                value={formAssetType}
                onChange={(e) => setFormAssetType(e.target.value)}
                className="input-field text-sm"
              >
                <option value="stock">Stock</option>
                <option value="etf">ETF</option>
                <option value="crypto">Crypto</option>
                <option value="commodity">Commodity</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Notes</label>
              <input
                type="text"
                value={formNotes}
                onChange={(e) => setFormNotes(e.target.value)}
                placeholder="Optional notes..."
                className="input-field text-sm"
              />
            </div>
          </div>
          <div className="flex items-center gap-2 justify-end">
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="btn-ghost text-sm"
            >
              Cancel
            </button>
            <button type="submit" disabled={submitting} className="btn-primary text-sm py-1.5">
              {submitting ? "Adding..." : "Add to Watchlist"}
            </button>
          </div>
        </form>
      )}

      {/* Watchlist table */}
      {items.length === 0 ? (
        <div className="card p-8 flex flex-col items-center justify-center text-center">
          <TrendingUp className="w-10 h-10 text-gray-600 mb-3" />
          <p className="text-gray-400 text-sm">Your watchlist is empty</p>
          <p className="text-gray-600 text-xs mt-1">
            Add symbols to track prices, sentiment, and signals
          </p>
        </div>
      ) : (
        <div className="card overflow-hidden">
          {/* Header */}
          <div className="grid grid-cols-[1fr_80px_100px_100px_100px_120px_40px] gap-3 px-5 py-2.5 border-b border-surface-border bg-surface text-xs text-gray-500 uppercase tracking-wider font-semibold">
            <span>Symbol</span>
            <span>Type</span>
            <span className="text-right">Price</span>
            <span className="text-right">Change</span>
            <span className="text-right">Mkt Cap</span>
            <span className="text-right">Added</span>
            <span />
          </div>

          {/* Rows */}
          <div className="divide-y divide-surface-border">
            {items.map((item) => {
              const md = marketDataMap[item.symbol];
              const change = md?.price_change_pct ?? 0;
              const isPositive = change >= 0;

              return (
                <div
                  key={item.id}
                  className="grid grid-cols-[1fr_80px_100px_100px_100px_120px_40px] gap-3 px-5 py-3 items-center hover:bg-surface-overlay transition-colors"
                >
                  {/* Symbol + name */}
                  <div>
                    <button
                      onClick={() => onSymbolClick(item.symbol)}
                      className="text-sm font-mono font-semibold text-accent hover:text-accent-hover transition-colors"
                    >
                      {item.symbol}
                    </button>
                    {item.notes && (
                      <p className="text-xs text-gray-600 truncate mt-0.5">
                        {item.notes}
                      </p>
                    )}
                  </div>

                  {/* Asset type */}
                  <span className="text-xs text-gray-500 capitalize">
                    {item.asset_type}
                  </span>

                  {/* Price */}
                  <span className="text-sm font-mono text-gray-200 text-right">
                    {md
                      ? `$${md.price?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? "—"}`
                      : "—"}
                  </span>

                  {/* Change */}
                  <span
                    className="text-sm font-mono text-right"
                    style={{ color: isPositive ? "#10B981" : "#EF4444" }}
                  >
                    {md
                      ? `${isPositive ? "+" : ""}${change.toFixed(2)}%`
                      : "—"}
                  </span>

                  {/* Market Cap */}
                  <span className="text-xs font-mono text-gray-400 text-right">
                    {md?.market_cap
                      ? formatLargeNumber(md.market_cap)
                      : "—"}
                  </span>

                  {/* Added date */}
                  <span className="text-xs text-gray-500 text-right">
                    {formatDistanceToNow(new Date(item.created_at), { addSuffix: true })}
                  </span>

                  {/* Remove */}
                  <button
                    onClick={() => handleRemove(item.id)}
                    className="text-gray-600 hover:text-red-400 transition-colors justify-self-end"
                    title="Remove from watchlist"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Analyses Tab ─────────────────────────────────────────────────────────────

function AnalysesTab({ topicId }: { topicId: string }) {
  const [analyses, setAnalyses] = useState<InvestmentAnalysisResponse[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getInvestmentAnalyses(topicId)
      .then(setAnalyses)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [topicId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (analyses.length === 0) {
    return (
      <div className="card p-8 flex flex-col items-center justify-center text-center">
        <BarChart3 className="w-10 h-10 text-gray-600 mb-3" />
        <p className="text-gray-400 text-sm">No investment analyses yet</p>
        <p className="text-gray-600 text-xs mt-1">
          Analyses are generated automatically when sufficient data is available
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
        Investment Analyses ({analyses.length})
      </h3>
      <div className="space-y-4">
        {analyses.map((analysis) => (
          <AnalysisCard key={analysis.id} analysis={analysis} />
        ))}
      </div>
    </div>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatLargeNumber(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1e12) return (n / 1e12).toFixed(2) + "T";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return n.toLocaleString();
}
