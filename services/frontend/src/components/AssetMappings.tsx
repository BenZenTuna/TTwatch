"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Link2,
  Check,
  X,
  Shield,
  ArrowRight,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import {
  getAssetMappings,
  verifyAssetMapping,
  rejectAssetMapping,
} from "@/lib/api-client";
import type { AssetMappingResponse } from "@/lib/types";

interface AssetMappingsProps {
  topicId: string;
  onSymbolClick?: (symbol: string) => void;
}

function getConfidenceColor(confidence: number): string {
  if (confidence >= 0.8) return "#10B981";
  if (confidence >= 0.5) return "#F59E0B";
  return "#EF4444";
}

function formatMethod(method: string | null): string {
  if (!method) return "Unknown";
  return method
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function AssetMappings({ topicId, onSymbolClick }: AssetMappingsProps) {
  const [mappings, setMappings] = useState<AssetMappingResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionInProgress, setActionInProgress] = useState<string | null>(null);

  const loadMappings = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getAssetMappings(topicId);
      setMappings(data);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [topicId]);

  useEffect(() => {
    loadMappings();
  }, [loadMappings]);

  async function handleVerify(mappingId: string) {
    setActionInProgress(mappingId);
    try {
      await verifyAssetMapping(mappingId);
      setMappings((prev) =>
        prev.map((m) =>
          m.id === mappingId ? { ...m, is_verified: true } : m
        )
      );
    } catch {
      // silent
    } finally {
      setActionInProgress(null);
    }
  }

  async function handleReject(mappingId: string) {
    setActionInProgress(mappingId);
    try {
      await rejectAssetMapping(mappingId);
      setMappings((prev) => prev.filter((m) => m.id !== mappingId));
    } catch {
      // silent
    } finally {
      setActionInProgress(null);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (mappings.length === 0) {
    return (
      <div className="card p-8 flex flex-col items-center justify-center text-center">
        <Link2 className="w-10 h-10 text-gray-600 mb-3" />
        <p className="text-gray-400 text-sm">No asset mappings found</p>
        <p className="text-gray-600 text-xs mt-1">
          Entity-to-ticker mappings will appear here as they are auto-resolved
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
        Asset Mappings
      </h3>

      <div className="card overflow-hidden">
        {/* Table header */}
        <div className="grid grid-cols-[1fr_auto_1fr_100px_100px_100px] gap-4 px-5 py-2.5 border-b border-surface-border bg-surface text-xs text-gray-500 uppercase tracking-wider font-semibold">
          <span>Entity</span>
          <span />
          <span>Symbol</span>
          <span>Confidence</span>
          <span>Method</span>
          <span className="text-right">Actions</span>
        </div>

        {/* Table rows */}
        <div className="divide-y divide-surface-border">
          {mappings.map((mapping) => (
            <div
              key={mapping.id}
              className="grid grid-cols-[1fr_auto_1fr_100px_100px_100px] gap-4 px-5 py-3 items-center hover:bg-surface-overlay transition-colors"
            >
              {/* Entity name */}
              <span className="text-sm text-gray-200 truncate">
                {mapping.entity_name}
              </span>

              {/* Arrow */}
              <ArrowRight className="w-3.5 h-3.5 text-gray-600" />

              {/* Symbol */}
              <div className="flex items-center gap-2">
                {mapping.resolved_symbol ? (
                  <button
                    onClick={() => onSymbolClick?.(mapping.resolved_symbol!)}
                    className="text-sm font-mono font-semibold text-accent hover:text-accent-hover transition-colors"
                  >
                    {mapping.resolved_symbol}
                  </button>
                ) : (
                  <span className="text-sm text-gray-500 italic">Unresolved</span>
                )}
                {mapping.is_verified && (
                  <span title="Verified">
                    <Shield className="w-3.5 h-3.5 text-emerald-400" />
                  </span>
                )}
              </div>

              {/* Confidence */}
              <div className="flex items-center gap-2">
                <div className="w-12 h-1.5 bg-surface rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${mapping.confidence * 100}%`,
                      backgroundColor: getConfidenceColor(mapping.confidence),
                    }}
                  />
                </div>
                <span
                  className="text-xs font-mono"
                  style={{ color: getConfidenceColor(mapping.confidence) }}
                >
                  {(mapping.confidence * 100).toFixed(0)}%
                </span>
              </div>

              {/* Method */}
              <span className="text-xs text-gray-500 truncate" title={mapping.resolution_method || undefined}>
                {formatMethod(mapping.resolution_method)}
              </span>

              {/* Actions */}
              <div className="flex items-center gap-1.5 justify-end">
                {!mapping.is_verified && (
                  <>
                    <button
                      onClick={() => handleVerify(mapping.id)}
                      disabled={actionInProgress === mapping.id}
                      className="p-1.5 rounded hover:bg-emerald-400/10 text-gray-500 hover:text-emerald-400 transition-colors disabled:opacity-50"
                      title="Verify mapping"
                    >
                      <Check className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => handleReject(mapping.id)}
                      disabled={actionInProgress === mapping.id}
                      className="p-1.5 rounded hover:bg-red-400/10 text-gray-500 hover:text-red-400 transition-colors disabled:opacity-50"
                      title="Reject mapping"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </>
                )}
                {mapping.is_verified && (
                  <span className="text-xs text-emerald-400 flex items-center gap-1">
                    <Shield className="w-3 h-3" />
                    Verified
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
