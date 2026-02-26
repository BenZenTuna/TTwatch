"use client";

import {
  TrendingUp,
  TrendingDown,
  Minus,
  AlertTriangle,
  Zap,
  FileText,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import type { InvestmentAnalysisResponse } from "@/lib/types";

interface AnalysisCardProps {
  analysis: InvestmentAnalysisResponse;
  compact?: boolean;
}

const RECOMMENDATION_CONFIG: Record<
  string,
  { label: string; color: string; bg: string; icon: React.ComponentType<{ className?: string }> }
> = {
  bullish: {
    label: "Bullish",
    color: "text-emerald-400",
    bg: "bg-emerald-400/10",
    icon: TrendingUp,
  },
  bearish: {
    label: "Bearish",
    color: "text-red-400",
    bg: "bg-red-400/10",
    icon: TrendingDown,
  },
  neutral: {
    label: "Neutral",
    color: "text-gray-400",
    bg: "bg-gray-400/10",
    icon: Minus,
  },
};

function getRecommendationConfig(rec: string | null) {
  if (!rec) return RECOMMENDATION_CONFIG.neutral;
  const key = rec.toLowerCase();
  return RECOMMENDATION_CONFIG[key] || RECOMMENDATION_CONFIG.neutral;
}

export function AnalysisCard({ analysis, compact = false }: AnalysisCardProps) {
  const recConfig = getRecommendationConfig(analysis.recommendation);
  const RecIcon = recConfig.icon;
  const confidence = analysis.confidence ?? 0;

  return (
    <div className="card p-5 space-y-4">
      {/* Header: recommendation badge + symbol + time */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-sm font-medium ${recConfig.color} ${recConfig.bg}`}>
            <RecIcon className="w-3.5 h-3.5" />
            {recConfig.label}
          </div>
          {analysis.symbol && (
            <span className="text-sm font-mono font-semibold text-gray-200">
              {analysis.symbol}
            </span>
          )}
          <span className="text-xs text-gray-500 capitalize">
            {analysis.analysis_scope} scope
          </span>
        </div>
        <span className="text-xs text-gray-500 whitespace-nowrap">
          {formatDistanceToNow(new Date(analysis.generated_at), { addSuffix: true })}
        </span>
      </div>

      {/* Confidence meter */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-gray-500">Confidence</span>
          <span className="text-xs font-mono text-gray-300">
            {(confidence * 100).toFixed(0)}%
          </span>
        </div>
        <div className="h-1.5 bg-surface rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${confidence * 100}%`,
              backgroundColor:
                confidence >= 0.7
                  ? "#10B981"
                  : confidence >= 0.4
                  ? "#F59E0B"
                  : "#EF4444",
            }}
          />
        </div>
      </div>

      {/* Analysis text */}
      {!compact && analysis.analysis_text && (
        <p className="text-sm text-gray-300 leading-relaxed">
          {analysis.analysis_text.length > 400
            ? analysis.analysis_text.slice(0, 400) + "..."
            : analysis.analysis_text}
        </p>
      )}

      {/* Key signals */}
      {analysis.key_signals.length > 0 && (
        <div>
          <h4 className="text-xs text-gray-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Zap className="w-3 h-3" />
            Key Signals
          </h4>
          <ul className="space-y-1">
            {analysis.key_signals.slice(0, compact ? 3 : undefined).map((signal, i) => (
              <li key={i} className="text-sm text-gray-400 flex items-start gap-2">
                <span className="text-emerald-400 mt-0.5">&bull;</span>
                {signal}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Risk factors */}
      {analysis.risk_factors.length > 0 && (
        <div>
          <h4 className="text-xs text-gray-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <AlertTriangle className="w-3 h-3" />
            Risk Factors
          </h4>
          <ul className="space-y-1">
            {analysis.risk_factors.slice(0, compact ? 3 : undefined).map((risk, i) => (
              <li key={i} className="text-sm text-gray-400 flex items-start gap-2">
                <span className="text-red-400 mt-0.5">&bull;</span>
                {risk}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Footer: articles considered + sentiment */}
      <div className="flex items-center gap-4 pt-2 border-t border-surface-border text-xs text-gray-500">
        <span className="flex items-center gap-1">
          <FileText className="w-3 h-3" />
          {analysis.articles_considered} article{analysis.articles_considered !== 1 ? "s" : ""} analyzed
        </span>
        {analysis.sentiment_score != null && (
          <span>
            Sentiment:{" "}
            <span
              className="font-mono"
              style={{
                color:
                  analysis.sentiment_score > 0.1
                    ? "#10B981"
                    : analysis.sentiment_score < -0.1
                    ? "#EF4444"
                    : "#6B7280",
              }}
            >
              {analysis.sentiment_score > 0 ? "+" : ""}
              {analysis.sentiment_score.toFixed(2)}
            </span>
          </span>
        )}
      </div>
    </div>
  );
}
