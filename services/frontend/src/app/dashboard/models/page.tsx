"use client";

import { useEffect, useState } from "react";
import {
  Cpu,
  Zap,
  RefreshCw,
  Check,
  Star,
} from "lucide-react";
import {
  getModelStatus,
  getTaskRouting,
  updateTaskRouting,
} from "@/lib/api-client";
import type {
  ModelInfo,
  ModelStatusResponse,
  TaskRoutingEntry,
  TaskRoutingChange,
} from "@/lib/types";

export default function ModelsPage() {
  const [modelStatus, setModelStatus] = useState<ModelStatusResponse | null>(
    null
  );
  const [routing, setRouting] = useState<TaskRoutingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      const [status, taskRouting] = await Promise.all([
        getModelStatus(),
        getTaskRouting(),
      ]);
      setModelStatus(status);
      setRouting(taskRouting.routing);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  async function handleRefresh() {
    setRefreshing(true);
    try {
      const status = await getModelStatus();
      setModelStatus(status);
    } catch {
      // ignore
    } finally {
      setRefreshing(false);
    }
  }

  async function handleTargetChange(
    taskCategory: string,
    newTarget: "primary" | "fast" | "auto"
  ) {
    setSaving(taskCategory);
    try {
      const result = await updateTaskRouting([
        { task_category: taskCategory, model_target: newTarget },
      ]);
      setRouting(result.routing);
    } catch {
      // ignore
    } finally {
      setSaving(null);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  const primaryModel = modelStatus?.models.find((m) => m.id === "primary");
  const fastModel = modelStatus?.models.find((m) => m.id === "fast");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-100">AI Models</h1>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-1.5 text-xs text-accent hover:text-accent/80 disabled:opacity-50 transition-colors"
        >
          <RefreshCw
            className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`}
          />
          Refresh status
        </button>
      </div>

      {/* Model Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {primaryModel && <ModelCard model={primaryModel} />}
        {fastModel && <ModelCard model={fastModel} />}
        {!primaryModel && !fastModel && (
          <div className="col-span-2 card p-6 text-center">
            <p className="text-sm text-gray-500">
              No local models configured. Running in{" "}
              {modelStatus?.provider || "cloud"} mode.
            </p>
          </div>
        )}
      </div>

      {/* Task Routing Table */}
      <div className="card p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
              Task Routing
            </h2>
            <p className="text-xs text-gray-600 mt-1">
              Choose which model handles each task. All tasks default to the
              fast model for speed.
            </p>
          </div>
        </div>

        <div className="space-y-1">
          {/* Header */}
          <div className="grid grid-cols-12 gap-3 px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider">
            <div className="col-span-5">Task</div>
            <div className="col-span-4">Description</div>
            <div className="col-span-3">Model</div>
          </div>

          {routing.map((entry) => (
            <TaskRoutingRow
              key={entry.task_category}
              entry={entry}
              saving={saving === entry.task_category}
              primaryName={primaryModel?.name || "Primary"}
              fastName={fastModel?.name || "Fast"}
              onTargetChange={handleTargetChange}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ModelCard({ model }: { model: ModelInfo }) {
  const isOnline = model.status === "online";
  const isLoading = model.status === "loading";
  const isPrimary = model.id === "primary";

  const statusColor = isOnline
    ? "bg-emerald-400"
    : isLoading
      ? "bg-amber-400"
      : "bg-red-400";
  const statusGlow = isOnline
    ? "shadow-emerald-400/20"
    : isLoading
      ? "shadow-amber-400/20"
      : "shadow-red-400/20";
  const statusText = isOnline ? "Online" : isLoading ? "Loading" : "Offline";

  return (
    <div
      className={`card p-5 border ${isOnline ? "border-surface-border" : "border-surface-border/50"}`}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2.5">
          {isPrimary ? (
            <Cpu className="w-5 h-5 text-violet-400" />
          ) : (
            <Zap className="w-5 h-5 text-amber-400" />
          )}
          <div>
            <h3 className="text-sm font-semibold text-gray-200">
              {model.name}
            </h3>
            <span
              className={`inline-block mt-0.5 text-[10px] font-medium uppercase tracking-wider px-1.5 py-0.5 rounded ${
                isPrimary
                  ? "bg-violet-500/10 text-violet-400"
                  : "bg-amber-500/10 text-amber-400"
              }`}
            >
              {isPrimary ? "Reasoning" : "Fast"}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <div
            className={`w-2 h-2 rounded-full ${statusColor} shadow-lg ${statusGlow} ${isLoading ? "animate-pulse" : ""}`}
          />
          <span className="text-xs text-gray-500">{statusText}</span>
        </div>
      </div>
      <p className="text-xs text-gray-500 leading-relaxed">
        {model.description}
      </p>
      <p className="text-[10px] text-gray-700 mt-2 font-mono truncate">
        {model.url}
      </p>
    </div>
  );
}

function TaskRoutingRow({
  entry,
  saving,
  primaryName,
  fastName,
  onTargetChange,
}: {
  entry: TaskRoutingEntry;
  saving: boolean;
  primaryName: string;
  fastName: string;
  onTargetChange: (
    category: string,
    target: "primary" | "fast" | "auto"
  ) => void;
}) {
  return (
    <div className="grid grid-cols-12 gap-3 items-center px-3 py-2.5 rounded-lg hover:bg-surface-overlay transition-colors">
      <div className="col-span-5 flex items-center gap-2">
        <span className="text-sm text-gray-200">{entry.display_name}</span>
        {entry.recommend_primary && (
          <span
            className="flex items-center gap-0.5 text-[10px] text-violet-400 bg-violet-500/10 px-1.5 py-0.5 rounded"
            title="The primary reasoning model produces meaningfully better results for this task"
          >
            <Star className="w-2.5 h-2.5" />
            Recommended
          </span>
        )}
      </div>
      <div className="col-span-4">
        <span className="text-xs text-gray-500">{entry.description}</span>
      </div>
      <div className="col-span-3 flex items-center gap-2">
        <select
          value={entry.model_target}
          onChange={(e) =>
            onTargetChange(
              entry.task_category,
              e.target.value as "primary" | "fast" | "auto"
            )
          }
          disabled={saving}
          className="w-full bg-surface-overlay border border-surface-border rounded-md px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-50"
        >
          <option value="fast">{fastName} (Fast)</option>
          <option value="primary">{primaryName} (Deep)</option>
          <option value="auto">Auto</option>
        </select>
        {saving && (
          <div className="w-3.5 h-3.5 border-2 border-accent border-t-transparent rounded-full animate-spin shrink-0" />
        )}
        {!saving && !entry.is_default && (
          <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
        )}
      </div>
    </div>
  );
}
