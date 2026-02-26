"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Newspaper,
  TrendingUp,
  Search,
  Settings,
  LogOut,
  Plus,
  Wifi,
  WifiOff,
  Shield,
} from "lucide-react";
import { useAppStore } from "@/lib/store";
import { getTopics, logout } from "@/lib/api-client";

interface SidebarProps {
  wsConnected: boolean;
}

export function Sidebar({ wsConnected }: SidebarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { topics, setTopics, selectedTopicId, selectTopic, user } =
    useAppStore();

  useEffect(() => {
    getTopics()
      .then(setTopics)
      .catch(() => {});
  }, [setTopics]);

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  const navItems = [
    { href: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
    { href: "/dashboard/articles", icon: Newspaper, label: "Articles" },
    { href: "/dashboard/investment", icon: TrendingUp, label: "Investment" },
    { href: "/dashboard/search", icon: Search, label: "Search" },
  ];

  return (
    <aside className="w-64 h-screen bg-surface-raised border-r border-surface-border flex flex-col fixed left-0 top-0">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 h-14 border-b border-surface-border shrink-0">
        <Shield className="w-5 h-5 text-accent" />
        <span className="font-bold text-gray-100 text-lg">TTwatch</span>
      </div>

      {/* Nav links */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
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
        <div className="pt-4 mt-4 border-t border-surface-border">
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
              <button
                key={topic.id}
                onClick={() => {
                  selectTopic(topic.id);
                  router.push(`/dashboard/topics/${topic.id}`);
                }}
                className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-colors ${
                  selectedTopicId === topic.id
                    ? "bg-accent/10 text-accent"
                    : "text-gray-400 hover:text-gray-200 hover:bg-surface-overlay"
                }`}
              >
                <span className="text-base">{topic.icon || "\u2022"}</span>
                <span className="truncate">{topic.name}</span>
              </button>
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
          <Settings className="w-4 h-4" />
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
