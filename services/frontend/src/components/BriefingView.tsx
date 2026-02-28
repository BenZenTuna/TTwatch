"use client";

import { useState } from "react";
import {
  FileText,
  Sparkles,
  AlertTriangle,
  Eye,
  ShieldAlert,
  RefreshCw,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import type { BriefingResponse } from "@/lib/types";
import { generateBriefing } from "@/lib/api-client";
import { format } from "date-fns";

interface BriefingViewProps {
  briefings: BriefingResponse[];
  topicId: string;
  onBriefingGenerated?: () => void;
}

export function BriefingView({
  briefings,
  topicId,
  onBriefingGenerated,
}: BriefingViewProps) {
  const [generating, setGenerating] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(
    briefings[0]?.id ?? null
  );

  async function handleGenerate() {
    setGenerating(true);
    try {
      await generateBriefing(topicId);
      onBriefingGenerated?.();
    } catch {
      // API error handled silently
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500">
        AI-generated briefings that summarize your articles into key highlights and actionable insights. Generate a new briefing anytime to get the latest analysis.
      </p>
      {/* Header with generate button */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Intelligence Briefings
        </h3>
        <button
          onClick={handleGenerate}
          disabled={generating}
          className="btn-primary text-sm flex items-center gap-2 disabled:opacity-50"
        >
          {generating ? (
            <>
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              Generating...
            </>
          ) : (
            <>
              <Sparkles className="w-3.5 h-3.5" />
              Generate New
            </>
          )}
        </button>
      </div>

      {briefings.length === 0 ? (
        <div className="card p-8 flex flex-col items-center justify-center text-center">
          <FileText className="w-10 h-10 text-gray-600 mb-3" />
          <p className="text-gray-400 text-sm">No briefings yet</p>
          <p className="text-gray-600 text-xs mt-1">
            Generate your first intelligence briefing to get started.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {briefings.map((briefing) => (
            <BriefingCard
              key={briefing.id}
              briefing={briefing}
              expanded={expanded === briefing.id}
              onToggle={() =>
                setExpanded(expanded === briefing.id ? null : briefing.id)
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

function BriefingCard({
  briefing,
  expanded,
  onToggle,
}: {
  briefing: BriefingResponse;
  expanded: boolean;
  onToggle: () => void;
}) {
  const highlights = briefing.highlights ?? [];
  const watchItems = briefing.watch_items ?? [];
  const newEntities = briefing.new_entities ?? [];
  const coverageGaps = briefing.coverage_gaps ?? [];

  return (
    <div className="card overflow-hidden">
      {/* Collapsible header */}
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between p-4 hover:bg-surface-overlay transition-colors text-left"
      >
        <div className="flex items-center gap-3">
          <FileText className="w-4 h-4 text-accent shrink-0" />
          <div>
            <p className="text-sm font-medium text-gray-200">
              Briefing &mdash;{" "}
              {format(new Date(briefing.generated_at), "MMM d, yyyy h:mm a")}
            </p>
            {!expanded && briefing.summary && (
              <p className="text-xs text-gray-500 mt-0.5 line-clamp-1">
                {briefing.summary}
              </p>
            )}
          </div>
        </div>
        {expanded ? (
          <ChevronUp className="w-4 h-4 text-gray-500 shrink-0" />
        ) : (
          <ChevronDown className="w-4 h-4 text-gray-500 shrink-0" />
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-4">
          {/* Executive summary */}
          {briefing.summary && (
            <div>
              <SectionHeading icon={FileText} label="Executive Summary" />
              <p className="text-sm text-gray-300 leading-relaxed">
                {briefing.summary}
              </p>
            </div>
          )}

          {/* Highlights */}
          {highlights.length > 0 && (
            <div>
              <SectionHeading icon={Sparkles} label="Key Highlights" />
              <ul className="space-y-1.5">
                {highlights.map((h, i) => (
                  <li
                    key={i}
                    className="text-sm text-gray-400 flex items-start gap-2"
                  >
                    <span className="text-accent mt-0.5 shrink-0">
                      &bull;
                    </span>
                    {h}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Watch items */}
          {watchItems.length > 0 && (
            <div>
              <SectionHeading icon={Eye} label="Watch Items" />
              <ul className="space-y-1.5">
                {watchItems.map((item, i) => (
                  <li
                    key={i}
                    className="text-sm text-gray-400 flex items-start gap-2"
                  >
                    <AlertTriangle className="w-3.5 h-3.5 text-amber-500 mt-0.5 shrink-0" />
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* New entities */}
          {newEntities.length > 0 && (
            <div>
              <SectionHeading icon={Sparkles} label="New Entities Detected" />
              <div className="flex flex-wrap gap-1.5">
                {newEntities.map((entity, i) => (
                  <span
                    key={i}
                    className="text-xs px-2 py-1 rounded-full bg-accent/10 text-accent border border-accent/20"
                  >
                    {typeof entity === "object" && entity !== null
                      ? (entity as { name?: string }).name ?? JSON.stringify(entity)
                      : String(entity)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Coverage gaps */}
          {coverageGaps.length > 0 && (
            <div>
              <SectionHeading icon={ShieldAlert} label="Coverage Gaps" />
              <ul className="space-y-1.5">
                {coverageGaps.map((gap, i) => (
                  <li
                    key={i}
                    className="text-sm text-gray-400 flex items-start gap-2"
                  >
                    <ShieldAlert className="w-3.5 h-3.5 text-red-400 mt-0.5 shrink-0" />
                    {typeof gap === "object" && gap !== null
                      ? (gap as { description?: string; name?: string }).description
                        ?? (gap as { name?: string }).name
                        ?? JSON.stringify(gap)
                      : String(gap)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SectionHeading({
  icon: Icon,
  label,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
}) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <Icon className="w-3.5 h-3.5 text-gray-500" />
      <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
        {label}
      </h4>
    </div>
  );
}
