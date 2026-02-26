"use client";

import { useEffect, useState, useCallback } from "react";
import { X, ArrowUpDown, Clock, BarChart3, SmilePlus } from "lucide-react";
import type { ClusterResponse, ArticleResponse } from "@/lib/types";
import { getClusterArticles } from "@/lib/api-client";
import { VELOCITY_COLORS, getSentimentColor } from "@/lib/design-tokens";
import { formatDistanceToNow } from "date-fns";

interface ClusterDetailProps {
  cluster: ClusterResponse | null;
  onClose: () => void;
}

type SortField = "recency" | "relevance" | "sentiment";
const PAGE_SIZE = 20;

const VELOCITY_ARROWS: Record<string, string> = {
  surging: "↑↑",
  rising: "↑",
  stable: "→",
  declining: "↓",
};

export function ClusterDetail({ cluster, onClose }: ClusterDetailProps) {
  const [articles, setArticles] = useState<ArticleResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [sortBy, setSortBy] = useState<SortField>("recency");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);

  const fetchArticles = useCallback(
    async (reset: boolean) => {
      if (!cluster) return;
      setLoading(true);
      const newOffset = reset ? 0 : offset;
      try {
        const data = await getClusterArticles(
          cluster.id,
          PAGE_SIZE,
          newOffset
        );
        if (reset) {
          setArticles(data);
          setOffset(PAGE_SIZE);
        } else {
          setArticles((prev) => [...prev, ...data]);
          setOffset((prev) => prev + PAGE_SIZE);
        }
        setHasMore(data.length === PAGE_SIZE);
      } catch {
        // API error handled silently
      } finally {
        setLoading(false);
      }
    },
    [cluster, offset]
  );

  // Fetch on cluster change
  useEffect(() => {
    if (cluster) {
      setArticles([]);
      setOffset(0);
      setHasMore(true);
      setSortBy("recency");
    }
  }, [cluster?.id]);

  useEffect(() => {
    if (cluster && articles.length === 0 && !loading) {
      fetchArticles(true);
    }
  }, [cluster?.id]);

  // Sort articles
  const sorted = [...articles].sort((a, b) => {
    switch (sortBy) {
      case "recency":
        return (
          new Date(b.published_at || b.ingested_at).getTime() -
          new Date(a.published_at || a.ingested_at).getTime()
        );
      case "relevance":
        return (b.relevance_score ?? 0) - (a.relevance_score ?? 0);
      case "sentiment":
        return (
          Math.abs(b.sentiment_score ?? 0) - Math.abs(a.sentiment_score ?? 0)
        );
      default:
        return 0;
    }
  });

  // Escape key to close
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    if (cluster) {
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }
  }, [cluster, onClose]);

  if (!cluster) return null;

  const velColor = cluster.velocity
    ? VELOCITY_COLORS[cluster.velocity] || "#6B7280"
    : "#6B7280";

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40 transition-opacity"
        onClick={onClose}
      />

      {/* Slide-in panel */}
      <div className="fixed top-0 right-0 h-full w-full max-w-lg bg-surface-raised border-l border-surface-border z-50 flex flex-col shadow-2xl animate-slide-in">
        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-surface-border shrink-0">
          <div>
            <div className="flex items-center gap-3">
              <div
                className="w-3 h-3 rounded-full shrink-0"
                style={{ backgroundColor: cluster.color || "#6B7280" }}
              />
              <h2 className="text-lg font-semibold text-gray-100">
                {cluster.keyword}
              </h2>
            </div>
            <div className="flex items-center gap-4 mt-2 text-sm text-gray-400">
              <span>{cluster.article_count} articles</span>
              <span className="flex items-center gap-1">
                <BarChart3 className="w-3.5 h-3.5" />
                score {cluster.trend_score.toFixed(1)}
              </span>
              {cluster.velocity && (
                <span style={{ color: velColor }}>
                  {VELOCITY_ARROWS[cluster.velocity] || ""} {cluster.velocity}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-300 transition-colors p-1"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Sort controls */}
        <div className="flex items-center gap-2 px-5 py-3 border-b border-surface-border shrink-0">
          <ArrowUpDown className="w-3.5 h-3.5 text-gray-500" />
          <span className="text-xs text-gray-500">Sort by:</span>
          {(["recency", "relevance", "sentiment"] as SortField[]).map(
            (field) => (
              <button
                key={field}
                onClick={() => setSortBy(field)}
                className={`text-xs px-2 py-1 rounded transition-colors ${
                  sortBy === field
                    ? "bg-accent/10 text-accent"
                    : "text-gray-400 hover:text-gray-200"
                }`}
              >
                {field}
              </button>
            )
          )}
        </div>

        {/* Article list */}
        <div className="flex-1 overflow-y-auto">
          {sorted.length === 0 && !loading ? (
            <div className="flex items-center justify-center h-32 text-gray-500 text-sm">
              No articles in this cluster
            </div>
          ) : (
            <div className="divide-y divide-surface-border">
              {sorted.map((article) => (
                <ArticleRow key={article.id} article={article} />
              ))}
            </div>
          )}

          {/* Load more */}
          {hasMore && articles.length > 0 && (
            <div className="p-4 flex justify-center">
              <button
                onClick={() => fetchArticles(false)}
                disabled={loading}
                className="text-sm text-accent hover:text-accent-hover transition-colors disabled:opacity-50"
              >
                {loading ? "Loading..." : "Load more"}
              </button>
            </div>
          )}

          {loading && articles.length === 0 && (
            <div className="flex items-center justify-center h-32">
              <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function ArticleRow({ article }: { article: ArticleResponse }) {
  const sentimentColor = article.sentiment_score
    ? getSentimentColor(article.sentiment_score)
    : null;

  return (
    <div className="px-5 py-3 hover:bg-surface-overlay transition-colors">
      <a
        href={article.url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sm font-medium text-gray-200 hover:text-accent transition-colors line-clamp-2"
      >
        {article.title}
      </a>
      <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-500">
        {article.source_name && <span>{article.source_name}</span>}
        {article.published_at && (
          <span className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatDistanceToNow(new Date(article.published_at), {
              addSuffix: true,
            })}
          </span>
        )}
        {sentimentColor && (
          <span
            className="flex items-center gap-1"
            style={{ color: sentimentColor }}
          >
            <SmilePlus className="w-3 h-3" />
            {article.sentiment_score!.toFixed(2)}
          </span>
        )}
      </div>
      {article.summary && (
        <p className="text-xs text-gray-500 mt-1.5 line-clamp-2">
          {article.summary}
        </p>
      )}
    </div>
  );
}
