import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";
import {
  getAccessToken,
  getRefreshToken,
  setTokens,
  clearTokens,
} from "./auth-storage";
import type {
  TokenResponse,
  UserResponse,
  TopicResponse,
  TopicCreate,
  ClusterResponse,
  ArticleResponse,
  ArticleDetailResponse,
  BriefingResponse,
  EntityResponse,
  SentimentPointResponse,
  SourceResponse,
  SavedQueryResponse,
  WatchlistItemResponse,
  WatchlistItemCreate,
  InvestmentAnalysisResponse,
  CorrelationSignalResponse,
  PriceAlertResponse,
  PriceAlertCreate,
  MarketDataResponse,
  PriceHistoryResponse,
  AssetMappingResponse,
  SearchResult,
} from "./types";

// SSR-safe base URL: Docker-internal on server, public on client
const API_BASE =
  typeof window === "undefined"
    ? process.env.INTERNAL_API_URL || "http://api:8080"
    : process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

const api = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
});

// Attach JWT to every request
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Auto-refresh on 401
let refreshPromise: Promise<TokenResponse> | null = null;

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retried?: boolean;
    };

    if (error.response?.status !== 401 || originalRequest._retried) {
      return Promise.reject(error);
    }

    const refreshToken = getRefreshToken();
    if (!refreshToken) {
      clearTokens();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
      return Promise.reject(error);
    }

    originalRequest._retried = true;

    // Deduplicate concurrent refresh calls
    if (!refreshPromise) {
      refreshPromise = axios
        .post<TokenResponse>(`${API_BASE}/auth/refresh`, {
          refresh_token: refreshToken,
        })
        .then((res) => res.data)
        .finally(() => {
          refreshPromise = null;
        });
    }

    try {
      const tokens = await refreshPromise;
      setTokens(tokens.access_token, tokens.refresh_token);
      originalRequest.headers.Authorization = `Bearer ${tokens.access_token}`;
      return api(originalRequest);
    } catch {
      clearTokens();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
      return Promise.reject(error);
    }
  }
);

// ── Auth ──

export async function login(
  email: string,
  password: string
): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/auth/login", {
    email,
    password,
  });
  setTokens(data.access_token, data.refresh_token);
  return data;
}

export async function register(
  email: string,
  displayName: string,
  password: string
): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/auth/register", {
    email,
    display_name: displayName,
    password,
  });
  setTokens(data.access_token, data.refresh_token);
  return data;
}

export async function logout(): Promise<void> {
  const refreshToken = getRefreshToken();
  if (refreshToken) {
    await api
      .post("/auth/logout", { refresh_token: refreshToken })
      .catch(() => {});
  }
  clearTokens();
}

// ── User ──

export async function getMe(): Promise<UserResponse> {
  const { data } = await api.get<UserResponse>("/api/me");
  return data;
}

// ── Topics ──

export async function getTopics(): Promise<TopicResponse[]> {
  const { data } = await api.get<TopicResponse[]>("/api/topics");
  return data;
}

export async function createTopic(
  topic: TopicCreate
): Promise<TopicResponse> {
  const { data } = await api.post<TopicResponse>("/api/topics", topic);
  return data;
}

export async function deleteTopic(topicId: string): Promise<void> {
  await api.delete(`/api/topics/${topicId}`);
}

// ── Clusters ──

export async function getTopicClusters(
  topicId: string
): Promise<ClusterResponse[]> {
  const { data } = await api.get<ClusterResponse[]>(
    `/api/topics/${topicId}/clusters`
  );
  return data;
}

export async function getClusterArticles(
  clusterId: string,
  limit = 20,
  offset = 0
): Promise<ArticleResponse[]> {
  const { data } = await api.get<ArticleResponse[]>(
    `/api/clusters/${clusterId}/articles`,
    { params: { limit, offset } }
  );
  return data;
}

// ── Articles ──

export async function getTopicArticles(
  topicId: string,
  params?: {
    cluster_id?: string;
    is_duplicate?: boolean;
    published_after?: string;
    published_before?: string;
    limit?: number;
    offset?: number;
  }
): Promise<ArticleResponse[]> {
  const { data } = await api.get<ArticleResponse[]>(
    `/api/topics/${topicId}/articles`,
    { params }
  );
  return data;
}

export async function getArticle(
  articleId: string
): Promise<ArticleDetailResponse> {
  const { data } = await api.get<ArticleDetailResponse>(
    `/api/articles/${articleId}`
  );
  return data;
}

// ── Search ──

export async function semanticSearch(
  query: string,
  topicId: string,
  limit = 20
): Promise<SearchResult[]> {
  const { data } = await api.post<SearchResult[]>("/api/search", {
    query,
    topic_id: topicId,
    limit,
  });
  return data;
}

// ── Briefings ──

export async function getTopicBriefings(
  topicId: string
): Promise<BriefingResponse[]> {
  const { data } = await api.get<BriefingResponse[]>(
    `/api/topics/${topicId}/briefings`
  );
  return data;
}

export async function generateBriefing(topicId: string): Promise<void> {
  await api.post(`/api/topics/${topicId}/briefings/generate`);
}

// ── Entities ──

export async function getTopicEntities(
  topicId: string,
  type?: string
): Promise<EntityResponse[]> {
  const { data } = await api.get<EntityResponse[]>(
    `/api/topics/${topicId}/entities`,
    { params: type ? { type } : undefined }
  );
  return data;
}

// ── Sentiment ──

export async function getTopicSentiment(
  topicId: string
): Promise<SentimentPointResponse[]> {
  const { data } = await api.get<SentimentPointResponse[]>(
    `/api/topics/${topicId}/sentiment`
  );
  return data;
}

export async function getSentimentHistory(
  topicId: string,
  params?: { cluster_keyword?: string; limit?: number }
): Promise<SentimentPointResponse[]> {
  const { data } = await api.get<SentimentPointResponse[]>(
    `/api/topics/${topicId}/sentiment/history`,
    { params }
  );
  return data;
}

// ── Sources ──

export async function getTopicSources(
  topicId: string
): Promise<SourceResponse[]> {
  const { data } = await api.get<SourceResponse[]>(
    `/api/topics/${topicId}/sources`
  );
  return data;
}

// ── Saved Queries ──

export async function getTopicQueries(
  topicId: string
): Promise<SavedQueryResponse[]> {
  const { data } = await api.get<SavedQueryResponse[]>(
    `/api/topics/${topicId}/queries`
  );
  return data;
}

// ── Investment ──

export async function getWatchlist(
  topicId: string
): Promise<WatchlistItemResponse[]> {
  const { data } = await api.get<WatchlistItemResponse[]>(
    `/api/topics/${topicId}/watchlist`
  );
  return data;
}

export async function getInvestmentAnalyses(
  topicId: string
): Promise<InvestmentAnalysisResponse[]> {
  const { data } = await api.get<InvestmentAnalysisResponse[]>(
    `/api/topics/${topicId}/analyses`
  );
  return data;
}

export async function getCorrelationSignals(
  topicId: string
): Promise<CorrelationSignalResponse[]> {
  const { data } = await api.get<CorrelationSignalResponse[]>(
    `/api/topics/${topicId}/correlation-signals`
  );
  return data;
}

export async function getPriceAlerts(): Promise<PriceAlertResponse[]> {
  const { data } = await api.get<PriceAlertResponse[]>("/api/price-alerts");
  return data;
}

export async function addWatchlistItem(
  topicId: string,
  item: WatchlistItemCreate
): Promise<WatchlistItemResponse> {
  const { data } = await api.post<WatchlistItemResponse>(
    `/api/topics/${topicId}/watchlist`,
    item
  );
  return data;
}

export async function removeWatchlistItem(itemId: string): Promise<void> {
  await api.delete(`/api/watchlist/${itemId}`);
}

export async function createPriceAlert(
  alert: PriceAlertCreate
): Promise<PriceAlertResponse> {
  const { data } = await api.post<PriceAlertResponse>(
    "/api/price-alerts",
    alert
  );
  return data;
}

export async function deletePriceAlert(alertId: string): Promise<void> {
  await api.delete(`/api/price-alerts/${alertId}`);
}

export async function getMarketData(
  symbol: string
): Promise<MarketDataResponse> {
  const { data } = await api.get<MarketDataResponse>(
    `/api/market-data/${symbol}`
  );
  return data;
}

export async function getPriceHistory(
  symbol: string,
  limit = 90
): Promise<PriceHistoryResponse[]> {
  const { data } = await api.get<PriceHistoryResponse[]>(
    `/api/market-data/${symbol}/history`,
    { params: { limit } }
  );
  return data;
}

export async function getAssetMappings(
  topicId: string
): Promise<AssetMappingResponse[]> {
  const { data } = await api.get<AssetMappingResponse[]>(
    `/api/topics/${topicId}/asset-mappings`
  );
  return data;
}

export async function verifyAssetMapping(mappingId: string): Promise<void> {
  await api.post(`/api/asset-mappings/${mappingId}/verify`);
}

export async function rejectAssetMapping(mappingId: string): Promise<void> {
  await api.post(`/api/asset-mappings/${mappingId}/reject`);
}

// ── Admin ──

export interface ServiceVersionStatus {
  name: string;
  env_var: string;
  current: string;
  latest: string | null;
  has_update: boolean;
  source_type: string;
  changelog_url: string;
}

export interface VersionCheckResponse {
  checked_at: string | null;
  services: ServiceVersionStatus[];
}

export async function getVersionStatus(): Promise<VersionCheckResponse> {
  const { data } = await api.get<VersionCheckResponse>(
    "/api/admin/versions"
  );
  return data;
}

export async function triggerVersionCheck(): Promise<VersionCheckResponse> {
  const { data } = await api.post<VersionCheckResponse>(
    "/api/admin/versions/check"
  );
  return data;
}

export default api;
