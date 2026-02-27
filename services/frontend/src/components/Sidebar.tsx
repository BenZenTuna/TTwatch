"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Newspaper,
  TrendingUp,
  Search,
  Cpu,
  Settings,
  LogOut,
  Plus,
  Wifi,
  WifiOff,
  Shield,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useAppStore } from "@/lib/store";
import { getTopics, logout, getVersionStatus, triggerTopicSearch, deleteTopic } from "@/lib/api-client";
import type { WSMessage } from "@/lib/types";

interface SidebarProps {
  wsConnected: boolean;
  lastWsMessage?: WSMessage | null;
}

export function Sidebar({ wsConnected, lastWsMessage }: SidebarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { topics, setTopics, selectedTopicId, selectTopic, user } =
    useAppStore();
  const [hasUpdates, setHasUpdates] = useState(false);
  const [searchingTopics, setSearchingTopics] = useState<Set<string>>(new Set());

  useEffect(() => {
    getTopics()
      .then(setTopics)
      .catch(() => {});
  }, [setTopics]);

  // Admin: check for available service updates
  useEffect(() => {
    if (!user?.is_admin) return;
    getVersionStatus()
      .then((data) => {
        const updates = data.services?.some((s) => s.has_update) ?? false;
        setHasUpdates(updates);
      })
      .catch(() => {});
  }, [user?.is_admin]);

  // Listen for search completion via WS to clear spinning state
  useEffect(() => {
    if (lastWsMessage?.type === "search_completed" && lastWsMessage.topic_id) {
      const tid = lastWsMessage.topic_id as string;
      setSearchingTopics((prev) => {
        const next = new Set(prev);
        next.delete(tid);
        return next;
      });
    }
  }, [lastWsMessage]);

  async function handleTopicSearch(e: React.MouseEvent, topicId: string) {
    e.stopPropagation();
    setSearchingTopics((prev) => new Set(prev).add(topicId));
    try {
      await triggerTopicSearch(topicId);
    } catch {
      // On error (including 429), stop spinning after a short delay
      setTimeout(() => {
        setSearchingTopics((prev) => {
          const next = new Set(prev);
          next.delete(topicId);
          return next;
        });
      }, 1000);
    }
  }

  async function handleDeleteTopic(e: React.MouseEvent, topicId: string, topicName: string) {
    e.stopPropagation();
    if (!confirm(`Delete "${topicName}"? This will remove all associated articles, clusters, and data.`)) {
      return;
    }
    try {
      await deleteTopic(topicId);
      const updated = await getTopics();
      setTopics(updated);
      // If we deleted the currently selected topic, navigate away
      if (selectedTopicId === topicId) {
        selectTopic(null);
        router.push("/dashboard");
      }
    } catch {
      // Silently handle — topic may already be deleted
    }
  }

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  const navItems = [
    { href: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
    { href: "/dashboard/articles", icon: Newspaper, label: "Articles" },
    { href: "/dashboard/investment", icon: TrendingUp, label: "Investment" },
    { href: "/dashboard/search", icon: Search, label: "Search" },
    { href: "/dashboard/models", icon: Cpu, label: "AI Models" },
  ];

  return (
    <aside className="w-64 h-screen bg-surface-raised border-r border-surface-border flex flex-col fixed left-0 top-0 overflow-hidden">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 h-14 border-b border-surface-border shrink-0">
        <Shield className="w-5 h-5 text-accent" />
        <span className="font-bold text-gray-100 text-lg">TTwatch</span>
      </div>

      {/* Nav links */}
      <nav className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden px-3 py-4 space-y-1">
        {navItems.map((item) => {
          const active =
            item.href === "/dashboard"
              ? pathname === "/dashboard"
              : pathname.startsWith(item.href);
          return (
            <button
              key={item.href}
              onClick={() => router.push(item.href)}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                active
                  ? "bg-accent/10 text-accent"
                  : "text-gray-400 hover:text-gray-200 hover:bg-surface-overlay"
              }`}
            >
              <item.icon className="w-4 h-4" />
              {item.label}
            </button>
          );
        })}

        {/* Topics section */}
        <div className="pt-4 mt-4 border-t border-surface-border overflow-hidden min-w-0">
          <div className="flex items-center justify-between px-3 mb-2">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
              Topics
            </span>
            <button
              onClick={() => router.push("/dashboard/topics/new")}
              className="text-gray-500 hover:text-gray-300 transition-colors"
              title="Create topic"
            >
              <Plus className="w-3.5 h-3.5" />
            </button>
          </div>
          {topics.length === 0 ? (
            <p className="px-3 text-xs text-gray-600">No topics yet</p>
          ) : (
            topics.map((topic) => (
              <div key={topic.id} className="flex items-center group min-w-0 overflow-hidden">
                <button
                  onClick={() => {
                    selectTopic(topic.id);
                    router.push(`/dashboard/topics/${topic.id}`);
                  }}
                  className={`flex-1 min-w-0 overflow-hidden flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-colors ${
                    selectedTopicId === topic.id
                      ? "bg-accent/10 text-accent"
                      : "text-gray-400 hover:text-gray-200 hover:bg-surface-overlay"
                  }`}
                >
                  <span className="text-base">{topic.icon || "\u2022"}</span>
                  <span className="truncate block">{topic.name}</span>
                </button>
                <button
                  onClick={(e) => handleTopicSearch(e, topic.id)}
                  disabled={searchingTopics.has(topic.id)}
                  className="opacity-0 group-hover:opacity-100 p-1 text-gray-500 hover:text-gray-300 transition-all disabled:opacity-100"
                  title="Search now"
                >
                  <RefreshCw className={`w-3 h-3 ${searchingTopics.has(topic.id) ? "animate-spin text-accent" : ""}`} />
                </button>
                <button
                  onClick={(e) => handleDeleteTopic(e, topic.id, topic.name)}
                  className="opacity-0 group-hover:opacity-100 p-1 text-gray-500 hover:text-red-400 transition-all"
                  title="Delete topic"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))
          )}
        </div>
      </nav>

      {/* Footer: connection status, settings, logout */}
      <div className="border-t border-surface-border px-3 py-3 space-y-1 shrink-0">
        {/* Connection indicator */}
        <div className="flex items-center gap-2 px-3 py-1 text-xs">
          {wsConnected ? (
            <>
              <Wifi className="w-3 h-3 text-emerald-400" />
              <span className="text-emerald-400">Live</span>
            </>
          ) : (
            <>
              <WifiOff className="w-3 h-3 text-gray-600" />
              <span className="text-gray-600">Offline</span>
            </>
          )}
        </div>

        <button
          onClick={() => router.push("/dashboard/settings")}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm text-gray-400 hover:text-gray-200 hover:bg-surface-overlay transition-colors"
        >
          <div className="relative">
            <Settings className="w-4 h-4" />
            {hasUpdates && (
              <span className="absolute -top-1 -right-1 w-2 h-2 bg-amber-400 rounded-full" />
            )}
          </div>
          Settings
        </button>

        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm text-gray-400 hover:text-red-400 hover:bg-surface-overlay transition-colors"
        >
          <LogOut className="w-4 h-4" />
          Sign out
          {user && (
            <span className="ml-auto text-xs text-gray-600 truncate max-w-[80px]">
              {user.display_name}
            </span>
          )}
        </button>
      </div>
    </aside>
  );
}
