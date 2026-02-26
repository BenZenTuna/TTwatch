"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { getAccessToken } from "@/lib/auth-storage";
import type { WSMessage } from "@/lib/types";

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8080/ws";

const MAX_BACKOFF = 30_000;
const INITIAL_BACKOFF = 1_000;

interface UseWebSocketOptions {
  onMessage?: (message: WSMessage) => void;
  enabled?: boolean;
}

interface UseWebSocketReturn {
  connected: boolean;
  lastMessage: WSMessage | null;
  send: (data: unknown) => void;
}

export function useWebSocket(
  options: UseWebSocketOptions = {}
): UseWebSocketReturn {
  const { onMessage, enabled = true } = options;
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef(INITIAL_BACKOFF);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    const token = getAccessToken();
    if (!token || !enabled) return;

    // Clean up existing connection
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      // Send auth message as required by the backend
      ws.send(JSON.stringify({ type: "auth", token }));
    };

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);

        if (msg.type === "connected") {
          setConnected(true);
          backoffRef.current = INITIAL_BACKOFF;
        } else if (msg.type === "ping") {
          ws.send(JSON.stringify({ type: "pong" }));
          return;
        }

        setLastMessage(msg);
        onMessageRef.current?.(msg);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = (event) => {
      setConnected(false);
      wsRef.current = null;

      // Don't reconnect on auth failure (4001) or intentional close
      if (event.code === 4001 || event.code === 1000) return;

      // Exponential backoff reconnect
      const delay = backoffRef.current;
      backoffRef.current = Math.min(delay * 2, MAX_BACKOFF);
      reconnectTimerRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      // onclose will fire after onerror, triggering reconnect
    };
  }, [enabled]);

  useEffect(() => {
    connect();

    return () => {
      clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) {
        wsRef.current.close(1000);
        wsRef.current = null;
      }
    };
  }, [connect]);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { connected, lastMessage, send };
}
