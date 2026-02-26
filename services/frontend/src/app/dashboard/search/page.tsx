"use client";

import { useState, useEffect } from "react";
import {
  Search,
  ExternalLink,
  Newspaper,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { useAppStore } from "@/lib/store";
import { semanticSearch } from "@/lib/api-client";
import type { SearchResult } from "@/lib/types";

export default function SearchPage() {
  const { topics, selectedTopicId, selectTopic } = useAppStore();
  const selectedTopic = topics.find((t) => t.id === selectedTopicId);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  // Auto-select first topic
  useEffect(() => {
    if (!selectedTopicId && topics.length > 0) {
      selectTopic(topics[0].id);
    }
  }, [topics, selectedTopicId, selectTopic]);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim() || !selectedTopicId) return;

    setLoading(true);
    setSearched(true);
    try {
      const data = await semanticSearch(query.trim(), selectedTopicId);
      setResults(data);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }

  if (topics.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] text-center">
        <Search className="w-12 h-12 text-gray-600 mb-4" />
        <h2 className="text-xl font-semibold text-gray-300 mb-2">
          No topics yet
        </h2>
        <p className="text-gray-500 max-w-md mb-4">
          Create an intelligence topic to start searching articles.
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
          <h1 className="text-2xl font-bold text-gray-100">Search</h1>
          {selectedTopic && (
            <p className="text-sm text-gray-500 mt-1">
              Semantic search across {selectedTopic.icon}{" "}
              {selectedTopic.name} articles
            </p>
          )}
        </div>

        {topics.length > 1 && (
          <div className="flex bg-surface-raised border border-surface-border rounded-lg p-1">
            {topics.map((topic) => (
              <button
                key={topic.id}
                onClick={() => {
                  selectTopic(topic.id);
                  setResults([]);
                  setSearched(false);
                }}
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

      {/* Search bar */}
      <form onSubmit={handleSearch} className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search articles by meaning..."
            className="input-field pl-10 text-sm w-full"
          />
        </div>
        <button
          type="submit"
          disabled={loading || !query.trim()}
          className="btn-primary text-sm px-5 disabled:opacity-50"
        >
          {loading ? "Searching..." : "Search"}
        </button>
      </form>

      {/* Results */}
      {loading ? (
        <div className="flex items-center justify-center h-32">
          <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      ) : searched && results.length === 0 ? (
        <div className="card p-8 flex flex-col items-center justify-center text-center">
          <Newspaper className="w-10 h-10 text-gray-600 mb-3" />
          <p className="text-gray-400 text-sm">No results found</p>
          <p className="text-gray-600 text-xs mt-1">
            Try a different query or broaden your search terms
          </p>
        </div>
      ) : results.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs text-gray-600">
            {results.length} result{results.length !== 1 ? "s" : ""}
          </p>
          {results.map((result) => (
            <SearchResultRow key={result.article.id} result={result} />
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center h-48 text-center">
          <Search className="w-10 h-10 text-gray-700 mb-3" />
          <p className="text-gray-500 text-sm">
            Enter a query to search across your articles
          </p>
          <p className="text-gray-600 text-xs mt-1">
            Uses vector similarity to find semantically relevant results
          </p>
        </div>
      )}
    </div>
  );
}

function SearchResultRow({ result }: { result: SearchResult }) {
  const { article, score } = result;

  return (
    <div className="card p-4 hover:bg-surface-overlay transition-colors">
      <div className="flex items-start gap-3">
        {/* Relevance bar */}
        <div className="flex flex-col items-center gap-1 shrink-0 pt-0.5">
          <div className="w-1.5 h-8 bg-surface-border rounded-full overflow-hidden">
            <div
              className="w-full bg-accent rounded-full transition-all"
              style={{ height: `${Math.round(score * 100)}%` }}
            />
          </div>
          <span className="text-[10px] text-gray-600 font-mono">
            {(score * 100).toFixed(0)}%
          </span>
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
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
          </div>
        </div>
      </div>
    </div>
  );
}
