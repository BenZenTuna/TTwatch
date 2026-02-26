"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Bell,
  BellRing,
  Plus,
  Trash2,
  ArrowUp,
  ArrowDown,
  ArrowUpRight,
  ArrowDownRight,
  X,
  CheckCircle2,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import {
  getPriceAlerts,
  createPriceAlert,
  deletePriceAlert,
} from "@/lib/api-client";
import type { PriceAlertResponse, PriceAlertCreate } from "@/lib/types";
import { useWebSocket } from "@/hooks/useWebSocket";
import type { WSMessage } from "@/lib/types";

const CONDITION_LABELS: Record<string, { label: string; icon: React.ComponentType<{ className?: string }> }> = {
  above: { label: "Above", icon: ArrowUp },
  below: { label: "Below", icon: ArrowDown },
  crosses_above: { label: "Crosses Above", icon: ArrowUpRight },
  crosses_below: { label: "Crosses Below", icon: ArrowDownRight },
};

export function PriceAlerts() {
  const [alerts, setAlerts] = useState<PriceAlertResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [toasts, setToasts] = useState<{ id: string; symbol: string; message: string }[]>([]);

  // Form state
  const [formSymbol, setFormSymbol] = useState("");
  const [formCondition, setFormCondition] = useState<PriceAlertCreate["condition"]>("above");
  const [formThreshold, setFormThreshold] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const loadAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getPriceAlerts();
      setAlerts(data);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAlerts();
  }, [loadAlerts]);

  // WebSocket: listen for alert triggers
  const handleWsMessage = useCallback((msg: WSMessage) => {
    if (msg.type === "alert" && msg.alert_type === "price_alert") {
      const symbol = (msg.symbol as string) || "Unknown";
      const message = (msg.message as string) || `Alert triggered for ${symbol}`;
      const id = Date.now().toString();
      setToasts((prev) => [...prev, { id, symbol, message }]);
      // Auto-dismiss after 8 seconds
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 8000);
      // Refresh alerts to update trigger status
      loadAlerts();
    }
  }, [loadAlerts]);

  useWebSocket({ onMessage: handleWsMessage });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!formSymbol.trim() || !formThreshold) return;

    setSubmitting(true);
    try {
      await createPriceAlert({
        symbol: formSymbol.trim().toUpperCase(),
        condition: formCondition,
        threshold: parseFloat(formThreshold),
      });
      setFormSymbol("");
      setFormThreshold("");
      setShowForm(false);
      await loadAlerts();
    } catch {
      // silent
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(alertId: string) {
    try {
      await deletePriceAlert(alertId);
      setAlerts((prev) => prev.filter((a) => a.id !== alertId));
    } catch {
      // silent
    }
  }

  return (
    <div className="space-y-4">
      {/* Toast notifications */}
      <div className="fixed top-4 right-4 z-50 space-y-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="flex items-center gap-3 bg-surface-raised border border-accent/30 rounded-lg px-4 py-3 shadow-lg animate-slide-in max-w-sm"
          >
            <BellRing className="w-4 h-4 text-accent shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-200">{toast.symbol}</p>
              <p className="text-xs text-gray-400 truncate">{toast.message}</p>
            </div>
            <button
              onClick={() => setToasts((prev) => prev.filter((t) => t.id !== toast.id))}
              className="text-gray-500 hover:text-gray-300"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>

      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Price Alerts
        </h3>
        <button
          onClick={() => setShowForm(!showForm)}
          className="btn-primary text-sm flex items-center gap-1.5 py-1.5 px-3"
        >
          <Plus className="w-3.5 h-3.5" />
          New Alert
        </button>
      </div>

      {/* Create alert form */}
      {showForm && (
        <form onSubmit={handleSubmit} className="card p-4 space-y-3">
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
              <label className="text-xs text-gray-500 mb-1 block">Condition</label>
              <select
                value={formCondition}
                onChange={(e) => setFormCondition(e.target.value as PriceAlertCreate["condition"])}
                className="input-field text-sm"
              >
                <option value="above">Above</option>
                <option value="below">Below</option>
                <option value="crosses_above">Crosses Above</option>
                <option value="crosses_below">Crosses Below</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">Threshold ($)</label>
              <input
                type="number"
                step="0.01"
                value={formThreshold}
                onChange={(e) => setFormThreshold(e.target.value)}
                placeholder="150.00"
                className="input-field text-sm"
                required
              />
            </div>
          </div>
          <div className="flex items-center gap-2 justify-end">
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="btn-ghost text-sm"
            >
              Cancel
            </button>
            <button type="submit" disabled={submitting} className="btn-primary text-sm py-1.5">
              {submitting ? "Creating..." : "Create Alert"}
            </button>
          </div>
        </form>
      )}

      {/* Alerts list */}
      {loading ? (
        <div className="flex items-center justify-center h-32">
          <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      ) : alerts.length === 0 ? (
        <div className="card p-8 flex flex-col items-center justify-center text-center">
          <Bell className="w-10 h-10 text-gray-600 mb-3" />
          <p className="text-gray-400 text-sm">No active price alerts</p>
          <p className="text-gray-600 text-xs mt-1">
            Create an alert to get notified when a price condition is met
          </p>
        </div>
      ) : (
        <div className="card divide-y divide-surface-border overflow-hidden">
          {alerts.map((alert) => {
            const condConfig = CONDITION_LABELS[alert.condition] || {
              label: alert.condition,
              icon: ArrowUp,
            };
            const CondIcon = condConfig.icon;

            return (
              <div
                key={alert.id}
                className="px-5 py-3 flex items-center justify-between hover:bg-surface-overlay transition-colors"
              >
                <div className="flex items-center gap-4">
                  <span className="text-sm font-mono font-semibold text-gray-200 w-16">
                    {alert.symbol}
                  </span>
                  <div className="flex items-center gap-1.5 text-sm text-gray-400">
                    <CondIcon className="w-3.5 h-3.5" />
                    <span>{condConfig.label}</span>
                    <span className="font-mono text-gray-200">
                      ${alert.threshold.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  {alert.last_known_price != null && (
                    <span className="text-xs text-gray-500">
                      Last: ${alert.last_known_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-3">
                  {alert.triggered_at ? (
                    <span className="flex items-center gap-1 text-xs text-emerald-400">
                      <CheckCircle2 className="w-3 h-3" />
                      Triggered{" "}
                      {formatDistanceToNow(new Date(alert.triggered_at), { addSuffix: true })}
                    </span>
                  ) : (
                    <span className="text-xs text-gray-500">
                      Created{" "}
                      {formatDistanceToNow(new Date(alert.created_at), { addSuffix: true })}
                    </span>
                  )}
                  <button
                    onClick={() => handleDelete(alert.id)}
                    className="text-gray-600 hover:text-red-400 transition-colors"
                    title="Delete alert"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
