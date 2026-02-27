"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, X, Sparkles } from "lucide-react";
import { useAppStore } from "@/lib/store";
import { createTopic, getTopics } from "@/lib/api-client";

const ICON_SUGGESTIONS = [
  "🔍", "📰", "💰", "🏛️", "🌍", "⚡", "🔬", "🏥",
  "🚀", "🛡️", "📊", "🤖", "🌱", "⚖️", "🎯", "🔗",
];

export default function NewTopicPage() {
  const router = useRouter();
  const { setTopics, selectTopic } = useAppStore();

  const [name, setName] = useState("");
  const [icon, setIcon] = useState("");
  const [searchTerms, setSearchTerms] = useState<string[]>([""]);
  const [refreshInterval, setRefreshInterval] = useState(120);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  function addSearchTerm() {
    setSearchTerms((prev) => [...prev, ""]);
  }

  function updateSearchTerm(index: number, value: string) {
    setSearchTerms((prev) => prev.map((t, i) => (i === index ? value : t)));
  }

  function removeSearchTerm(index: number) {
    setSearchTerms((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;

    setSubmitting(true);
    setError("");

    try {
      const terms = searchTerms
        .map((t) => t.trim())
        .filter((t) => t.length > 0);

      const topic = await createTopic({
        name: name.trim(),
        icon: icon || null,
        config: terms.length > 0 ? { search_terms: terms } : {},
        refresh_interval_minutes: refreshInterval,
      });

      // Refresh topics in store and navigate
      const updated = await getTopics();
      setTopics(updated);
      selectTopic(topic.id);
      router.push(`/dashboard/topics/${topic.id}`);
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Failed to create topic";
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-100">
          Create New Topic
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Define an intelligence topic to monitor. TTwatch will automatically
          search, collect, and analyze articles.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Name */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1.5">
            Topic Name <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. AI Regulation, Cryptocurrency Markets, Climate Policy"
            className="input-field text-sm"
            required
            autoFocus
          />
          <p className="text-xs text-gray-600 mt-1">
            The system will auto-generate optimized search queries from this name using AI
          </p>
        </div>

        {/* Icon */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1.5">
            Icon
          </label>
          <div className="flex items-center gap-3">
            <input
              type="text"
              value={icon}
              onChange={(e) => setIcon(e.target.value)}
              placeholder="Emoji"
              className="input-field text-sm w-20 text-center text-lg"
              maxLength={2}
            />
            <div className="flex flex-wrap gap-1.5">
              {ICON_SUGGESTIONS.map((emoji) => (
                <button
                  key={emoji}
                  type="button"
                  onClick={() => setIcon(emoji)}
                  className={`w-8 h-8 rounded-md text-lg flex items-center justify-center transition-colors ${
                    icon === emoji
                      ? "bg-accent/20 ring-1 ring-accent"
                      : "bg-surface hover:bg-surface-overlay"
                  }`}
                >
                  {emoji}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Search Terms */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1.5">
            Additional Search Terms
          </label>
          <p className="text-xs text-gray-600 mb-2">
            Optional extra queries to broaden article discovery. The system also auto-generates
            queries from the topic name — you can view and edit all queries later on the topic page.
          </p>
          <div className="space-y-2">
            {searchTerms.map((term, i) => (
              <div key={i} className="flex items-center gap-2">
                <input
                  type="text"
                  value={term}
                  onChange={(e) => updateSearchTerm(i, e.target.value)}
                  placeholder={`e.g. ${
                    i === 0
                      ? '"EU AI Act"'
                      : i === 1
                      ? "artificial intelligence policy"
                      : "search term"
                  }`}
                  className="input-field text-sm flex-1"
                />
                {searchTerms.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeSearchTerm(i)}
                    className="text-gray-600 hover:text-red-400 transition-colors p-1"
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>
            ))}
            <button
              type="button"
              onClick={addSearchTerm}
              className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-300 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              Add search term
            </button>
          </div>
        </div>

        {/* Refresh Interval */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-1.5">
            Refresh Interval
          </label>
          <select
            value={refreshInterval}
            onChange={(e) => setRefreshInterval(Number(e.target.value))}
            className="input-field text-sm w-48"
          >
            <option value={30}>Every 30 minutes</option>
            <option value={60}>Every hour</option>
            <option value={120}>Every 2 hours</option>
            <option value={360}>Every 6 hours</option>
            <option value={720}>Every 12 hours</option>
            <option value={1440}>Daily</option>
          </select>
        </div>

        {/* Error */}
        {error && (
          <div className="text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">
            {error}
          </div>
        )}

        {/* Submit */}
        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={submitting || !name.trim()}
            className="btn-primary text-sm px-6 py-2.5 flex items-center gap-2 disabled:opacity-50"
          >
            <Sparkles className="w-4 h-4" />
            {submitting ? "Creating..." : "Create Topic"}
          </button>
          <button
            type="button"
            onClick={() => router.back()}
            className="text-sm text-gray-500 hover:text-gray-300 transition-colors px-4 py-2.5"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
