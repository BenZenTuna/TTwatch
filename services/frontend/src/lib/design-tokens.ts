// Matches CLUSTER_COLORS from services/worker/worker/tasks/cluster.py
export const CLUSTER_COLORS = [
  "#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
  "#EC4899", "#06B6D4", "#84CC16", "#F97316", "#6366F1",
  "#14B8A6", "#E11D48", "#A855F7", "#0EA5E9", "#D946EF",
] as const;

export function getClusterColor(index: number): string {
  return CLUSTER_COLORS[index % CLUSTER_COLORS.length];
}

export const SURFACE = {
  base: "#0f1117",
  raised: "#161923",
  overlay: "#1e2130",
  border: "#2a2d3e",
} as const;

export const TYPOGRAPHY = {
  fontSans: "'Inter', system-ui, sans-serif",
  fontMono: "'JetBrains Mono', 'Fira Code', monospace",
  sizes: {
    xs: "0.75rem",
    sm: "0.875rem",
    base: "1rem",
    lg: "1.125rem",
    xl: "1.25rem",
    "2xl": "1.5rem",
    "3xl": "1.875rem",
  },
} as const;

export const SPACING = {
  sidebar: "16rem",
  headerHeight: "3.5rem",
  cardPadding: "1.25rem",
  sectionGap: "1.5rem",
} as const;

// Sentiment color scale: negative (red) → neutral (gray) → positive (green)
export function getSentimentColor(score: number): string {
  if (score <= -0.3) return "#EF4444";
  if (score <= -0.1) return "#F97316";
  if (score < 0.1) return "#6B7280";
  if (score < 0.3) return "#84CC16";
  return "#10B981";
}

// Velocity indicator colors
export const VELOCITY_COLORS: Record<string, string> = {
  surging: "#EF4444",
  rising: "#F59E0B",
  stable: "#6B7280",
  declining: "#3B82F6",
};
