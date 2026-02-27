// TypeScript types matching backend Pydantic schemas

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
}

export interface UserResponse {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
  is_admin: boolean;
  max_topics: number;
  max_articles_per_topic: number;
  max_api_keys: number;
  created_at: string;
  last_login_at: string | null;
}

export interface TopicResponse {
  id: string;
  name: string;
  icon: string | null;
  config: Record<string, unknown>;
  refresh_interval_minutes: number;
  last_refreshed_at: string | null;
  created_at: string;
}

export interface TopicCreate {
  name: string;
  icon?: string | null;
  config?: Record<string, unknown>;
  refresh_interval_minutes?: number;
}

export interface ClusterResponse {
  id: string;
  keyword: string;
  color: string | null;
  article_count: number;
  trend_score: number;
  velocity: string | null;
}

export interface ArticleResponse {
  id: string;
  url: string;
  title: string;
  source_name: string | null;
  published_at: string | null;
  ingested_at: string;
  summary: string | null;
  sentiment_score: number | null;
  relevance_score: number | null;
  cluster_id: string | null;
  is_duplicate: boolean;
}

export interface ArticleDetailResponse extends ArticleResponse {
  source_url: string | null;
  key_quotes: string[];
  duplicate_of: string | null;
}

export interface BriefingResponse {
  id: string;
  generated_at: string;
  summary: string | null;
  highlights: string[];
  new_entities: string[];
  watch_items: string[];
  coverage_gaps: string[];
}

export interface EntityResponse {
  id: string;
  name: string;
  type: string;
  topic_id: string;
  first_seen: string;
}

export interface EntityNodeResponse {
  id: string;
  name: string;
  type: string;
  article_count: number;
}

export interface EntityEdgeResponse {
  source: string;
  target: string;
  weight: number;
}

export interface EntityGraphResponse {
  entities: EntityNodeResponse[];
  edges: EntityEdgeResponse[];
}

export interface SentimentPointResponse {
  cluster_id: string;
  cluster_keyword: string;
  period_start: string;
  avg_sentiment: number;
  article_count: number;
}

export interface SourceResponse {
  id: string;
  topic_id: string;
  name: string;
  url: string;
  source_type: string;
  enabled: boolean;
  is_builtin: boolean;
  config: Record<string, unknown>;
}

export interface SavedQueryResponse {
  id: string;
  topic_id: string;
  query_text: string;
  schedule: string;
  last_run: string | null;
  last_result_count: number | null;
  created_at: string;
}

export interface WatchlistItemResponse {
  id: string;
  symbol: string;
  asset_type: string;
  added_reason: string | null;
  topic_id: string;
  notes: string | null;
  target_price: number | null;
  stop_loss: number | null;
  created_at: string;
}

export interface InvestmentAnalysisResponse {
  id: string;
  topic_id: string;
  analysis_scope: string;
  scope_ref_id: string | null;
  symbol: string;
  analysis_text: string;
  recommendation: string | null;
  confidence: number | null;
  key_signals: string[];
  risk_factors: string[];
  articles_considered: number;
  sentiment_score: number | null;
  generated_at: string;
}

export interface CorrelationSignalResponse {
  id: string;
  topic_id: string;
  cluster_id: string;
  symbol: string;
  signal_type: string;
  signal_strength: number;
  description: string;
  detected_at: string;
}

export interface PriceAlertResponse {
  id: string;
  symbol: string;
  condition: string;
  threshold: number;
  last_known_price: number | null;
  is_active: boolean;
  triggered_at: string | null;
  created_at: string;
}

export interface MarketDataResponse {
  id: string;
  symbol: string;
  asset_type: string;
  price: number;
  price_change_pct: number | null;
  volume: number | null;
  market_cap: number | null;
  pe_ratio: number | null;
  eps: number | null;
  dividend_yield: number | null;
  beta: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
  data_source: string;
  is_stale: boolean;
  fetched_at: string;
}

export interface PriceHistoryResponse {
  symbol: string;
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  adj_close: number | null;
  volume: number | null;
}

export interface AssetMappingResponse {
  id: string;
  entity_name: string;
  resolved_symbol: string | null;
  resolution_method: string | null;
  confidence: number;
  is_verified: boolean;
  created_at: string;
  updated_at: string;
}

export interface WatchlistItemCreate {
  symbol: string;
  asset_type: string;
  added_reason?: string | null;
  notes?: string | null;
  target_price?: number | null;
  stop_loss?: number | null;
}

export interface PriceAlertCreate {
  symbol: string;
  condition: "above" | "below" | "crosses_above" | "crosses_below";
  threshold: number;
}

export interface SearchResult {
  article: ArticleResponse;
  score: number;
}

// Processing status
export interface ProcessingStatusResponse {
  phase: "ingesting" | "processing" | "clustering" | "complete" | "idle";
  total_articles: number;
  embedded: number;
  summarized: number;
  sentiment: number;
  relevance: number;
  clustered: number;
  cluster_count: number;
}

// Search status
export interface SearchStatusResponse {
  status: "idle" | "searching" | "completed" | "error";
  started_at?: string;
  completed_at?: string;
  articles_found?: number;
  error?: string;
}

// WebSocket message types
export interface WSMessage {
  type: string;
  [key: string]: unknown;
}

export interface WSConnectedMessage extends WSMessage {
  type: "connected";
  user_id: string;
}

export interface WSPingMessage extends WSMessage {
  type: "ping";
}

export interface WSAlertMessage extends WSMessage {
  type: "alert";
  user_id: string;
}

export interface WSSearchCompletedMessage extends WSMessage {
  type: "search_completed";
  topic_id: string;
  articles_found: number;
  completed_at: string;
}
