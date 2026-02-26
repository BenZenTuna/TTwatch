"use client";

import { useEffect, useState } from "react";
import {
  Newspaper,
  ExternalLink,
  Filter,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { useAppStore } from "@/lib/store";
import { getTopicArticles, getTopicClusters } from "@/lib/api-client";
import type { ArticleResponse, ClusterResponse } from "@/lib/types";

const PAGE_SIZE = 25;

export default function ArticlesPage() {
  const { topics, selectedTopicId, selectTopic } = useAppStore();
  const selectedTopic = topics.find((t) => t.id === selectedTopicId);

  const [articles, setArticles] = useState<ArticleResponse[]>([]);
  const [clusters, setClusters] = useState<ClusterResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [offset, setOffset] = useState(0);

  // Filters
  const [filterCluster, setFilterCluster] = useState<string>("");
  const [hideDuplicates, setHideDuplicates] = useState(true);

  // Auto-select first topic
  useEffect(() => {
    if (!selectedTopicId && topics.length > 0) {
      selectTopic(topics[0].id);
    }
  }, [topics, selectedTopicId, selectTopic]);

  // Load clusters for filter dropdown
  useEffect(() => {
    if (!selectedTopicId) return;
    getTopicClusters(selectedTopicId).then(setClusters).catch(() => {});
  }, [selectedTopicId]);

  // Load articles
  useEffect(() => {
    if (!selectedTopicId) return;
    setLoading(true);
    getTopicArticles(selectedTopicId, {
      cluster_id: filterCluster || undefined,
      is_duplicate: hideDuplicates ? false : undefined,
      limit: PAGE_SIZE,
      offset,
    })
      .then(setArticles)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [selectedTopicId, filterCluster, hideDuplicates, offset]);

  // Reset offset when filters change
  useEffect(() => {
    setOffset(0);
  }, [selectedTopicId, filterCluster, hideDuplicates]);

  if (topics.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] text-center">
        <Newspaper className="w-12 h-12 text-gray-600 mb-4" />
        <h2 className="text-xl font-semibold text-gray-300 mb-2">
          No topics yet
        </h2>
        <p className="text-gray-500 max-w-md mb-4">
          Create an intelligence topic to start collecting articles.
        </p>
        <a
          href="/dashboard/topics/new"
          className="btn-primary text-sm px-5 py-2"
        >
          Create your first topic
        </a>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">Articles</h1>
          {selectedTopic && (
            <p className="text-sm text-gray-500 mt-1">
              {selectedTopic.icon} {selectedTopic.name}
            </p>
          )}
        </div>

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

      {/* Filters */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-gray-500" />
          <select
            value={filterCluster}
            onChange={(e) => setFilterCluster(e.target.value)}
            className="input-field text-sm py-1.5"
          >
            <option value="">All clusters</option>
            {clusters.map((c) => (
              <option key={c.id} value={c.id}>
                {c.keyword} ({c.article_count})
              </option>
            ))}
          </select>
        </div>

        <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            checked={hideDuplicates}
            onChange={(e) => setHideDuplicates(e.target.checked)}
            className="rounded border-surface-border bg-surface text-accent focus:ring-accent"
          />
          Hide duplicates
        </label>
      </div>

      {/* Article list */}
      {loading ? (
        <div className="flex items-center justify-center h-32">
          <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      ) : articles.length === 0 ? (
        <div className="card p-8 flex flex-col items-center justify-center text-center">
          <Newspaper className="w-10 h-10 text-gray-600 mb-3" />
          <p className="text-gray-400 text-sm">No articles found</p>
          <p className="text-gray-600 text-xs mt-1">
            Articles will appear here after the next search cycle
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {articles.map((article) => (
            <ArticleRow key={article.id} article={article} clusters={clusters} />
          ))}
        </div>
      )}

      {/* Pagination */}
      {articles.length > 0 && (
        <div className="flex items-center justify-between">
          <button
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0}
            className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft className="w-4 h-4" />
            Previous
          </button>
          <span className="text-xs text-gray-600">
            Showing {offset + 1}–{offset + articles.length}
          </span>
          <button
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={articles.length < PAGE_SIZE}
            className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Next
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}

function ArticleRow({
  article,
  clusters,
}: {
  article: ArticleResponse;
  clusters: ClusterResponse[];
}) {
  const cluster = clusters.find((c) => c.id === article.cluster_id);

  return (
    <div className="card p-4 hover:bg-surface-overlay transition-colors">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            {cluster && (
              <span
                className="inline-block w-2 h-2 rounded-full shrink-0"
                style={{ backgroundColor: cluster.color || "#6B7280" }}
                title={cluster.keyword}
              />
            )}
            <a
              href={article.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium text-gray-200 hover:text-accent transition-colors truncate"
            >
              {article.title}
            </a>
            <ExternalLink className="w-3 h-3 text-gray-600 shrink-0" />
          </div>

          {article.summary && (
            <p className="text-xs text-gray-500 line-clamp-2 mb-2">
              {article.summary}
            </p>
          )}

          <div className="flex items-center gap-3 text-xs text-gray-600">
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
                style={{
                  color:
                    article.sentiment_score > 0.2
                      ? "#10B981"
                      : article.sentiment_score < -0.2
                      ? "#EF4444"
                      : "#6B7280",
                }}
              >
                Sentiment: {article.sentiment_score.toFixed(2)}
              </span>
            )}
            {cluster && (
              <span className="text-gray-600">{cluster.keyword}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
