"use client";

import { useEffect, useState } from "react";
import {
  Newspaper,
  Layers,
  FileText,
  TrendingUp,
  Bell,
  RefreshCw,
} from "lucide-react";
import { useAppStore } from "@/lib/store";
import {
  getTopicClusters,
  getTopicBriefings,
  getTopicArticles,
} from "@/lib/api-client";
import { VELOCITY_COLORS } from "@/lib/design-tokens";
import { formatDistanceToNow } from "date-fns";

export default function DashboardPage() {
  const {
    topics,
    selectedTopicId,
    selectTopic,
    clusters,
    setClusters,
    latestBriefing,
    setLatestBriefing,
    pendingUpdates,
    clearUpdates,
  } = useAppStore();

  const [articleCount, setArticleCount] = useState<number>(0);
  const [loading, setLoading] = useState(false);

  const selectedTopic = topics.find((t) => t.id === selectedTopicId);

  // Auto-select first topic if none selected
  useEffect(() => {
    if (!selectedTopicId && topics.length > 0) {
      selectTopic(topics[0].id);
    }
  }, [topics, selectedTopicId, selectTopic]);

  // Fetch data when topic changes
  useEffect(() => {
    if (!selectedTopicId) return;

    setLoading(true);
    clearUpdates();

    Promise.all([
      getTopicClusters(selectedTopicId).then(setClusters),
      getTopicBriefings(selectedTopicId).then((briefings) => {
        setLatestBriefing(briefings.length > 0 ? briefings[0] : null);
      }),
      getTopicArticles(selectedTopicId, { limit: 1 }).then((articles) => {
        // The API doesn't return total count directly, so we use the response
        // as an indicator that articles exist. A proper count endpoint could be added later.
        setArticleCount(articles.length > 0 ? clusters.reduce((sum, c) => sum + c.article_count, 0) : 0);
      }),
    ])
      .catch(() => {})
      .finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTopicId]);

  // Update article count from clusters when they load
  useEffect(() => {
    if (clusters.length > 0) {
      setArticleCount(clusters.reduce((sum, c) => sum + c.article_count, 0));
    }
  }, [clusters]);

  if (topics.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] text-center">
        <Layers className="w-12 h-12 text-gray-600 mb-4" />
        <h2 className="text-xl font-semibold text-gray-300 mb-2">
          No topics yet
        </h2>
        <p className="text-gray-500 max-w-md">
          Create your first intelligence topic to start monitoring news, trends,
          and market signals.
        </p>
      </div>
    );
  }

  // Sort clusters: highest trend_score first
  const trendingClusters = [...clusters]
    .sort((a, b) => b.trend_score - a.trend_score)
    .slice(0, 6);

  return (
    <div className="space-y-6">
      {/* Header with topic selector */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">Dashboard</h1>
          {selectedTopic && (
            <p className="text-sm text-gray-500 mt-1">
              {selectedTopic.icon} {selectedTopic.name}
              {selectedTopic.last_refreshed_at && (
                <span className="ml-2">
                  &middot; Updated{" "}
                  {formatDistanceToNow(new Date(selectedTopic.last_refreshed_at), {
                    addSuffix: true,
                  })}
                </span>
              )}
            </p>
          )}
        </div>

        <div className="flex items-center gap-3">
          {/* Real-time update badge */}
          {pendingUpdates > 0 && (
            <button
              onClick={() => {
                clearUpdates();
                // Re-fetch data
                if (selectedTopicId) {
                  getTopicClusters(selectedTopicId).then(setClusters);
                }
              }}
              className="flex items-center gap-2 bg-accent/10 text-accent px-3 py-1.5 rounded-full text-sm hover:bg-accent/20 transition-colors"
            >
              <Bell className="w-3.5 h-3.5" />
              {pendingUpdates} new update{pendingUpdates !== 1 ? "s" : ""}
              <RefreshCw className="w-3 h-3" />
            </button>
          )}

          {/* Topic selector tabs */}
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
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <>
          {/* Stats cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <StatCard
              icon={Newspaper}
              label="Total Articles"
              value={articleCount.toLocaleString()}
            />
            <StatCard
              icon={Layers}
              label="Clusters"
              value={clusters.length.toString()}
            />
            <StatCard
              icon={FileText}
              label="Latest Briefing"
              value={
                latestBriefing
                  ? formatDistanceToNow(new Date(latestBriefing.generated_at), {
                      addSuffix: true,
                    })
                  : "None"
              }
            />
          </div>

          {/* Latest briefing summary */}
          {latestBriefing?.summary && (
            <div className="card p-5">
              <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
                Latest Briefing
              </h3>
              <p className="text-gray-200 text-sm leading-relaxed">
                {latestBriefing.summary}
              </p>
              {(latestBriefing.highlights ?? []).length > 0 && (
                <ul className="mt-3 space-y-1">
                  {(latestBriefing.highlights ?? []).slice(0, 4).map((h, i) => (
                    <li
                      key={i}
                      className="text-sm text-gray-400 flex items-start gap-2"
                    >
                      <span className="text-accent mt-0.5">&bull;</span>
                      {h}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Trending clusters */}
          {trendingClusters.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
                Top Trending Clusters
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {trendingClusters.map((cluster) => (
                  <div
                    key={cluster.id}
                    className="card p-4 flex items-start gap-3"
                  >
                    <div
                      className="w-1 h-full min-h-[3rem] rounded-full shrink-0"
                      style={{ backgroundColor: cluster.color || "#6B7280" }}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <h4 className="text-sm font-medium text-gray-200 truncate">
                          {cluster.keyword}
                        </h4>
                        {cluster.velocity && (
                          <span
                            className="text-xs px-1.5 py-0.5 rounded shrink-0"
                            style={{
                              color:
                                VELOCITY_COLORS[cluster.velocity] || "#6B7280",
                              backgroundColor: `${
                                VELOCITY_COLORS[cluster.velocity] || "#6B7280"
                              }15`,
                            }}
                          >
                            {cluster.velocity}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
                        <span>{cluster.article_count} articles</span>
                        <span className="flex items-center gap-1">
                          <TrendingUp className="w-3 h-3" />
                          {cluster.trend_score.toFixed(1)}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="card p-4 flex items-center gap-4">
      <div className="bg-accent/10 p-2.5 rounded-lg">
        <Icon className="w-5 h-5 text-accent" />
      </div>
      <div>
        <p className="text-xs text-gray-500 uppercase tracking-wider">
          {label}
        </p>
        <p className="text-lg font-semibold text-gray-100 mt-0.5">{value}</p>
      </div>
    </div>
  );
}
