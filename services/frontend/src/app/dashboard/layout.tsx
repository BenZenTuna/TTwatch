"use client";

import { useEffect } from "react";
import { AuthGuard } from "@/components/AuthGuard";
import { Sidebar } from "@/components/Sidebar";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useAppStore } from "@/lib/store";
import { getMe } from "@/lib/api-client";
import type { WSMessage } from "@/lib/types";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { setUser, incrementUpdates } = useAppStore();

  useEffect(() => {
    getMe()
      .then(setUser)
      .catch(() => {});
  }, [setUser]);

  function handleWsMessage(msg: WSMessage) {
    if (msg.type !== "connected" && msg.type !== "ping") {
      incrementUpdates();
    }
  }

  const { connected, lastMessage } = useWebSocket({ onMessage: handleWsMessage });

  return (
    <AuthGuard>
      <div className="flex min-h-screen">
        <Sidebar wsConnected={connected} lastWsMessage={lastMessage} />
        <main className="flex-1 ml-64 p-6">{children}</main>
      </div>
    </AuthGuard>
  );
}
