"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import {
  Layers,
  Newspaper,
  FileText,
  Users,
  Activity,
  RefreshCw,
  Bell,
  Search,
  AlertCircle,
} from "lucide-react";
import { useAppStore } from "@/lib/store";
import { useWebSocket } from "@/hooks/useWebSocket";
import {
  getTopicClusters,
  getTopicBriefings,
  getTopicArticles,
  getEntityGraph,
  getSentimentHistory,
  triggerTopicSearch,
  getTopicSearchStatus,
} from "@/lib/api-client";
import type {
  ClusterResponse,
  BriefingResponse,
  ArticleResponse,
  EntityGraphResponse,
  SentimentPointResponse,
  SearchStatusResponse,
  WSMessage,
} from "@/lib/types";
import { BubbleCluster } from "@/components/BubbleCluster";
import { TrendChart } from "@/components/TrendChart";
import { SentimentTimeline } from "@/components/SentimentTimeline";
import { ClusterDetail } from "@/components/ClusterDetail";
import { BriefingView } from "@/components/BriefingView";
import { EntityNetwork } from "@/components/EntityNetwork";
import { formatDistanceToNow } from "date-fns";

type Tab = "overview" | "articles" | "briefings" | "entities" | "sentiment";

const TABS: { id: Tab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: "overview", label: "Overview", icon: Layers },
  { id: "articles", label: "Articles", icon: Newspaper },
  { id: "briefings", label: "Briefings", icon: FileText },
  { id: "entities", label: "Entities", icon: Users },
  { id: "sentiment", label: "Sentiment", icon: Activity },
];

const ARTICLE_PAGE_SIZE = 30;

export default function TopicPage() {
  const params = useParams();
  const topicId = params.id as string;

  const { topics, selectTopic } = useAppStore();
  const topic = topics.find((t) => t.id === topicId);

  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [loading, setLoading] = useState(true);
  const [pendingWsUpdates, setPendingWsUpdates] = useState(0);

  // Data states
  const [clusters, setClusters] = useState<ClusterResponse[]>([]);
  const [briefings, setBriefings] = useState<BriefingResponse[]>([]);
  const [articles, setArticles] = useState<ArticleResponse[]>([]);
  const [entityGraph, setEntityGraph] = useState<EntityGraphResponse | null>(null);
  const [sentimentData, setSentimentData] = useState<SentimentPointResponse[]>([]);

  // Search status
  const [searchStatus, setSearchStatus] = useState<SearchStatusResponse>({ status: "idle" });
  const [searchError, setSearchError] = useState<string | null>(null);
  const [completedMessage, setCompletedMessage] = useState<string | null>(null);
  const completedTimerRef = useRef<ReturnType<typeof setTimeout>>();

  // Cluster detail panel
  const [selectedCluster, setSelectedCluster] = useState<ClusterResponse | null>(null);

  // Article pagination
  const [articleOffset, setArticleOffset] = useState(0);
  const [hasMoreArticles, setHasMoreArticles] = useState(true);

  // Select this topic in global store
  useEffect(() => {
    selectTopic(topicId);
  }, [topicId, selectTopic]);

  // Track whether we need to refresh after search completion (set by WS, consumed by effect)
  const [searchJustCompleted, setSearchJustCompleted] = useState(false);

  // WebSocket: real-time updates
  const handleWsMessage = useCallback((msg: WSMessage) => {
    if (msg.type === "search_completed" && msg.topic_id === topicId) {
      const found = msg.articles_found as number;
      setSearchStatus({ status: "completed", articles_found: found });
      setCompletedMessage(`Found ${found} article${found !== 1 ? "s" : ""}`);
      clearTimeout(completedTimerRef.current);
      completedTimerRef.current = setTimeout(() => setCompletedMessage(null), 5000);
      setSearchJustCompleted(true);
      return;
    }
    if (msg.type !== "connected" && msg.type !== "ping") {
      setPendingWsUpdates((prev) => prev + 1);
    }
  }, [topicId]);

  useWebSocket({ onMessage: handleWsMessage });

  // Initial data load
  const loadCoreData = useCallback(async () => {
    setLoading(true);
    try {
      const [clusterData, briefingData] = await Promise.all([
        getTopicClusters(topicId),
        getTopicBriefings(topicId),
      ]);
      setClusters(clusterData);
      setBriefings(briefingData);
    } catch {
      // API error handled silently
    } finally {
      setLoading(false);
    }
  }, [topicId]);

  useEffect(() => {
    loadCoreData();
  }, [loadCoreData]);

  // Refresh data when search completes via WebSocket
  useEffect(() => {
    if (searchJustCompleted) {
      setSearchJustCompleted(false);
      loadCoreData();
    }
  }, [searchJustCompleted, loadCoreData]);

  // Poll search status while searching
  useEffect(() => {
    getTopicSearchStatus(topicId).then(setSearchStatus).catch(() => {});
  }, [topicId]);

  useEffect(() => {
    if (searchStatus.status !== "searching") return;
    const interval = setInterval(() => {
      getTopicSearchStatus(topicId).then((s) => {
        setSearchStatus(s);
        if (s.status === "completed") {
          setCompletedMessage(`Found ${s.articles_found ?? 0} article${s.articles_found !== 1 ? "s" : ""}`);
          clearTimeout(completedTimerRef.current);
          completedTimerRef.current = setTimeout(() => setCompletedMessage(null), 5000);
          loadCoreData();
        }
      }).catch(() => {});
    }, 5000);
    return () => clearInterval(interval);
  }, [searchStatus.status, topicId, loadCoreData]);

  // Cleanup completed message timer
  useEffect(() => {
    return () => clearTimeout(completedTimerRef.current);
  }, []);

  // Handle search trigger
  const handleSearchNow = useCallback(async () => {
    setSearchError(null);
    try {
      await triggerTopicSearch(topicId);
      setSearchStatus({ status: "searching", started_at: new Date().toISOString() });
    } catch (err: unknown) {
      const error = err as { response?: { status?: number; data?: { detail?: string } } };
      if (error.response?.status === 429) {
        setSearchError(error.response.data?.detail || "Please wait before searching again.");
      } else {
        setSearchError("Failed to trigger search.");
      }
    }
  }, [topicId]);

  // Load tab-specific data lazily
  useEffect(() => {
    if (activeTab === "articles" && articles.length === 0) {
      getTopicArticles(topicId, { limit: ARTICLE_PAGE_SIZE, offset: 0 })
        .then((data) => {
          setArticles(data);
          setArticleOffset(ARTICLE_PAGE_SIZE);
          setHasMoreArticles(data.length === ARTICLE_PAGE_SIZE);
        })
        .catch(() => {});
    }
    if (activeTab === "entities" && !entityGraph) {
      getEntityGraph(topicId).then(setEntityGraph).catch(() => {});
    }
    if (activeTab === "sentiment" && sentimentData.length === 0) {
      getSentimentHistory(topicId).then(setSentimentData).catch(() => {});
    }
  }, [activeTab, topicId]);

  // Load more articles
  const loadMoreArticles = useCallback(async () => {
    try {
      const data = await getTopicArticles(topicId, {
        limit: ARTICLE_PAGE_SIZE,
        offset: articleOffset,
      });
      setArticles((prev) => [...prev, ...data]);
      setArticleOffset((prev) => prev + ARTICLE_PAGE_SIZE);
      setHasMoreArticles(data.length === ARTICLE_PAGE_SIZE);
    } catch {
      // silently ignore
    }
  }, [topicId, articleOffset]);

  // Refresh on WS update
  const handleRefresh = useCallback(() => {
    setPendingWsUpdates(0);
    loadCoreData();
    // Also refresh the current tab's data
    if (activeTab === "articles") {
      getTopicArticles(topicId, { limit: ARTICLE_PAGE_SIZE, offset: 0 })
        .then((data) => {
          setArticles(data);
          setArticleOffset(ARTICLE_PAGE_SIZE);
          setHasMoreArticles(data.length === ARTICLE_PAGE_SIZE);
        })
        .catch(() => {});
    }
    if (activeTab === "entities") {
      getEntityGraph(topicId).then(setEntityGraph).catch(() => {});
    }
    if (activeTab === "sentiment") {
      getSentimentHistory(topicId).then(setSentimentData).catch(() => {});
    }
  }, [topicId, activeTab, loadCoreData]);

  // Briefing regenerated callback
  const handleBriefingGenerated = useCallback(() => {
    getTopicBriefings(topicId).then(setBriefings).catch(() => {});
  }, [topicId]);

  if (!topic) {
    return (
      <div className="flex items-center justify-center h-[60vh] text-gray-500">
        Topic not found
      </div>
    );
  }

  const totalArticles = clusters.reduce((s, c) => s + c.article_count, 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">
            {topic.icon && <span className="mr-2">{topic.icon}</span>}
            {topic.name}
          </h1>
          {topic.last_refreshed_at && (
            <p className="text-sm text-gray-500 mt-1">
              Updated{" "}
              {formatDistanceToNow(new Date(topic.last_refreshed_at), {
                addSuffix: true,
              })}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2">
          {pendingWsUpdates > 0 && (
            <button
              onClick={handleRefresh}
              className="flex items-center gap-2 bg-accent/10 text-accent px-3 py-1.5 rounded-full text-sm hover:bg-accent/20 transition-colors"
            >
              <Bell className="w-3.5 h-3.5" />
              {pendingWsUpdates} update{pendingWsUpdates !== 1 ? "s" : ""}
              <RefreshCw className="w-3 h-3" />
            </button>
          )}

          <button
            onClick={handleSearchNow}
            disabled={searchStatus.status === "searching"}
            className="flex items-center gap-2 bg-surface-raised border border-surface-border text-gray-300 px-3 py-1.5 rounded-lg text-sm hover:bg-surface-overlay transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Search className={`w-3.5 h-3.5 ${searchStatus.status === "searching" ? "animate-spin" : ""}`} />
            {searchStatus.status === "searching" ? "Searching..." : "Search Now"}
          </button>
        </div>
      </div>

      {/* Search status indicator */}
      {searchStatus.status === "searching" && (
        <div className="flex items-center gap-2 text-sm text-accent bg-accent/5 border border-accent/20 rounded-lg px-4 py-2">
          <div className="w-2 h-2 bg-accent rounded-full animate-pulse" />
          Searching for articles...
        </div>
      )}
      {completedMessage && (
        <div className="flex items-center gap-2 text-sm text-emerald-400 bg-emerald-400/5 border border-emerald-400/20 rounded-lg px-4 py-2">
          {completedMessage}
        </div>
      )}
      {searchStatus.status === "error" && (
        <div className="flex items-center gap-2 text-sm text-red-400 bg-red-400/5 border border-red-400/20 rounded-lg px-4 py-2">
          <AlertCircle className="w-3.5 h-3.5" />
          Search failed: {searchStatus.error || "Unknown error"}
        </div>
      )}
      {searchError && (
        <div className="flex items-center gap-2 text-sm text-amber-400 bg-amber-400/5 border border-amber-400/20 rounded-lg px-4 py-2">
          <AlertCircle className="w-3.5 h-3.5" />
          {searchError}
        </div>
      )}

      {/* Tab bar */}
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

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <>
          {/* ── Overview Tab ── */}
          {activeTab === "overview" && (
            <div className="space-y-6">
              {/* Stats row */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="card p-4">
                  <p className="text-xs text-gray-500 uppercase tracking-wider">
                    Articles
                  </p>
                  <p className="text-lg font-semibold text-gray-100 mt-0.5">
                    {totalArticles.toLocaleString()}
                  </p>
                </div>
                <div className="card p-4">
                  <p className="text-xs text-gray-500 uppercase tracking-wider">
                    Clusters
                  </p>
                  <p className="text-lg font-semibold text-gray-100 mt-0.5">
                    {clusters.length}
                  </p>
                </div>
                <div className="card p-4">
                  <p className="text-xs text-gray-500 uppercase tracking-wider">
                    Latest Briefing
                  </p>
                  <p className="text-lg font-semibold text-gray-100 mt-0.5">
                    {briefings.length > 0
                      ? formatDistanceToNow(
                          new Date(briefings[0].generated_at),
                          { addSuffix: true }
                        )
                      : "None"}
                  </p>
                </div>
              </div>

              {/* Bubble cluster + Trend chart side by side */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="card p-4">
                  <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
                    Cluster Map
                  </h3>
                  <BubbleCluster
                    clusters={clusters}
                    onClusterClick={setSelectedCluster}
                  />
                </div>
                <div className="card p-4">
                  <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
                    Trend Rankings
                  </h3>
                  <TrendChart
                    clusters={clusters}
                    onClusterClick={setSelectedCluster}
                  />
                </div>
              </div>

              {/* Latest briefing summary */}
              {briefings.length > 0 && briefings[0].summary && (
                <div className="card p-5">
                  <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
                    Latest Briefing
                  </h3>
                  <p className="text-sm text-gray-300 leading-relaxed">
                    {briefings[0].summary}
                  </p>
                  {briefings[0].highlights.length > 0 && (
                    <ul className="mt-3 space-y-1">
                      {briefings[0].highlights.slice(0, 4).map((h, i) => (
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
            </div>
          )}

          {/* ── Articles Tab ── */}
          {activeTab === "articles" && (
            <div className="space-y-3">
              {articles.length === 0 ? (
                <div className="card p-8 flex flex-col items-center justify-center text-center">
                  <Newspaper className="w-10 h-10 text-gray-600 mb-3" />
                  <p className="text-gray-400 text-sm">No articles yet</p>
                </div>
              ) : (
                <>
                  <div className="divide-y divide-surface-border card overflow-hidden">
                    {articles.map((article) => (
                      <div
                        key={article.id}
                        className="px-5 py-3 hover:bg-surface-overlay transition-colors"
                      >
                        <a
                          href={article.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm font-medium text-gray-200 hover:text-accent transition-colors line-clamp-2"
                        >
                          {article.title}
                        </a>
                        <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
                          {article.source_name && (
                            <span>{article.source_name}</span>
                          )}
                          {article.published_at && (
                            <span>
                              {formatDistanceToNow(
                                new Date(article.published_at),
                                { addSuffix: true }
                              )}
                            </span>
                          )}
                          {article.sentiment_score != null && (
                            <span>
                              sentiment: {article.sentiment_score.toFixed(2)}
                            </span>
                          )}
                        </div>
                        {article.summary && (
                          <p className="text-xs text-gray-500 mt-1 line-clamp-2">
                            {article.summary}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>

                  {hasMoreArticles && (
                    <div className="flex justify-center">
                      <button
                        onClick={loadMoreArticles}
                        className="text-sm text-accent hover:text-accent-hover transition-colors"
                      >
                        Load more articles
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* ── Briefings Tab ── */}
          {activeTab === "briefings" && (
            <BriefingView
              briefings={briefings}
              topicId={topicId}
              onBriefingGenerated={handleBriefingGenerated}
            />
          )}

          {/* ── Entities Tab ── */}
          {activeTab === "entities" && (
            <div className="card p-4">
              <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
                Entity Network
              </h3>
              <EntityNetwork
                graph={entityGraph || { entities: [], edges: [] }}
              />
            </div>
          )}

          {/* ── Sentiment Tab ── */}
          {activeTab === "sentiment" && (
            <div className="card p-4">
              <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
                Sentiment Timeline
              </h3>
              <SentimentTimeline data={sentimentData} />
            </div>
          )}
        </>
      )}

      {/* Cluster detail slide-in */}
      <ClusterDetail
        cluster={selectedCluster}
        onClose={() => setSelectedCluster(null)}
      />
    </div>
  );
}
