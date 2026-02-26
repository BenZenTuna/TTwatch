import { create } from "zustand";
import type {
  TopicResponse,
  ClusterResponse,
  BriefingResponse,
  UserResponse,
} from "./types";

interface AppState {
  // User
  user: UserResponse | null;
  setUser: (user: UserResponse | null) => void;

  // Topics
  topics: TopicResponse[];
  setTopics: (topics: TopicResponse[]) => void;
  selectedTopicId: string | null;
  selectTopic: (id: string | null) => void;

  // Clusters for selected topic
  clusters: ClusterResponse[];
  setClusters: (clusters: ClusterResponse[]) => void;

  // Latest briefing for selected topic
  latestBriefing: BriefingResponse | null;
  setLatestBriefing: (briefing: BriefingResponse | null) => void;

  // Real-time update counter
  pendingUpdates: number;
  incrementUpdates: () => void;
  clearUpdates: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),

  topics: [],
  setTopics: (topics) => set({ topics }),
  selectedTopicId: null,
  selectTopic: (id) =>
    set({ selectedTopicId: id, clusters: [], latestBriefing: null, pendingUpdates: 0 }),

  clusters: [],
  setClusters: (clusters) => set({ clusters }),

  latestBriefing: null,
  setLatestBriefing: (briefing) => set({ latestBriefing: briefing }),

  pendingUpdates: 0,
  incrementUpdates: () =>
    set((state) => ({ pendingUpdates: state.pendingUpdates + 1 })),
  clearUpdates: () => set({ pendingUpdates: 0 }),
}));
