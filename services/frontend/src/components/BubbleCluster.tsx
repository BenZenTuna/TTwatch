"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import * as d3 from "d3";
import type { ClusterResponse } from "@/lib/types";
import { getClusterColor, VELOCITY_COLORS } from "@/lib/design-tokens";
import {
  BubbleNode,
  computeRadius,
  createBubbleSimulation,
} from "@/lib/force-simulation";

interface BubbleClusterProps {
  clusters: ClusterResponse[];
  onClusterClick?: (cluster: ClusterResponse) => void;
}

export function BubbleCluster({
  clusters,
  onClusterClick,
}: BubbleClusterProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const simulationRef = useRef<d3.Simulation<BubbleNode, undefined> | null>(
    null
  );
  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    cluster: ClusterResponse;
  } | null>(null);
  const [dimensions, setDimensions] = useState({ width: 600, height: 400 });

  // Responsive sizing
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        const { width } = entry.contentRect;
        setDimensions({ width, height: Math.max(300, width * 0.6) });
      }
    });

    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const handleClusterClick = useCallback(
    (cluster: ClusterResponse) => {
      onClusterClick?.(cluster);
    },
    [onClusterClick]
  );

  // D3 force simulation
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || clusters.length === 0) return;

    const { width, height } = dimensions;

    // Compute radius domain
    const counts = clusters.map((c) => c.article_count);
    const domain: [number, number] = [
      Math.min(...counts, 1),
      Math.max(...counts, 1),
    ];

    // Create nodes
    const nodes: BubbleNode[] = clusters.map((c, i) => ({
      id: c.id,
      keyword: c.keyword,
      articleCount: c.article_count,
      color: c.color || getClusterColor(i),
      trendScore: c.trend_score,
      velocity: c.velocity,
      radius: computeRadius(c.article_count, domain),
    }));

    // Stop existing simulation
    simulationRef.current?.stop();

    // Build D3 scene
    const svgSelection = d3.select(svg);
    svgSelection.selectAll("*").remove();

    const g = svgSelection.append("g");

    // Bubble groups
    const bubbles = g
      .selectAll<SVGGElement, BubbleNode>("g.bubble")
      .data(nodes, (d) => d.id)
      .join(
        (enter) => {
          const group = enter
            .append("g")
            .attr("class", "bubble")
            .style("cursor", "pointer")
            .attr("opacity", 0);

          // Circle
          group
            .append("circle")
            .attr("r", 0)
            .attr("fill", (d) => d.color)
            .attr("fill-opacity", 0.7)
            .attr("stroke", (d) => d.color)
            .attr("stroke-width", 2)
            .attr("stroke-opacity", 0.9)
            .transition()
            .duration(600)
            .attr("r", (d) => d.radius);

          // Label (only for large enough bubbles)
          group
            .append("text")
            .attr("text-anchor", "middle")
            .attr("dy", "-0.2em")
            .attr("fill", "#e5e7eb")
            .attr("font-size", (d) =>
              d.radius > 35 ? "11px" : d.radius > 25 ? "9px" : "0px"
            )
            .attr("font-weight", 500)
            .attr("pointer-events", "none")
            .text((d) =>
              d.keyword.length > 14
                ? d.keyword.slice(0, 12) + "..."
                : d.keyword
            );

          // Article count sublabel
          group
            .append("text")
            .attr("text-anchor", "middle")
            .attr("dy", "1em")
            .attr("fill", "#9ca3af")
            .attr("font-size", (d) => (d.radius > 30 ? "9px" : "0px"))
            .attr("pointer-events", "none")
            .text((d) => `${d.articleCount}`);

          group.transition().duration(600).attr("opacity", 1);

          return group;
        },
        (update) => update,
        (exit) =>
          exit.transition().duration(300).attr("opacity", 0).remove()
      );

    // Hover and click handlers
    bubbles
      .on("mouseenter", function (event: MouseEvent, d: BubbleNode) {
        d3.select(this)
          .select("circle")
          .transition()
          .duration(150)
          .attr("fill-opacity", 0.9)
          .attr("stroke-width", 3);

        const rect = svg.getBoundingClientRect();
        const cluster = clusters.find((c) => c.id === d.id);
        if (cluster) {
          setTooltip({
            x: event.clientX - rect.left,
            y: event.clientY - rect.top - 10,
            cluster,
          });
        }
      })
      .on("mousemove", function (event: MouseEvent) {
        const rect = svg.getBoundingClientRect();
        setTooltip((prev) =>
          prev
            ? {
                ...prev,
                x: event.clientX - rect.left,
                y: event.clientY - rect.top - 10,
              }
            : null
        );
      })
      .on("mouseleave", function () {
        d3.select(this)
          .select("circle")
          .transition()
          .duration(150)
          .attr("fill-opacity", 0.7)
          .attr("stroke-width", 2);
        setTooltip(null);
      })
      .on("click", function (_event: MouseEvent, d: BubbleNode) {
        const cluster = clusters.find((c) => c.id === d.id);
        if (cluster) handleClusterClick(cluster);
      });

    // Simulation
    const simulation = createBubbleSimulation(nodes, width, height);
    simulationRef.current = simulation;

    simulation.on("tick", () => {
      bubbles.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

    return () => {
      simulation.stop();
    };
  }, [clusters, dimensions, handleClusterClick]);

  const velocityLabel = (v: string | null) => {
    if (!v) return "";
    const arrows: Record<string, string> = {
      surging: "↑↑",
      rising: "↑",
      stable: "→",
      declining: "↓",
    };
    return arrows[v] || "";
  };

  return (
    <div ref={containerRef} className="relative w-full">
      <svg
        ref={svgRef}
        width={dimensions.width}
        height={dimensions.height}
        className="w-full"
      />
      {tooltip && (
        <div
          className="absolute pointer-events-none z-10 bg-surface-overlay border border-surface-border rounded-lg px-3 py-2 shadow-lg"
          style={{
            left: tooltip.x,
            top: tooltip.y,
            transform: "translate(-50%, -100%)",
          }}
        >
          <p className="text-sm font-medium text-gray-100">
            {tooltip.cluster.keyword}
          </p>
          <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
            <span>{tooltip.cluster.article_count} articles</span>
            <span>score {tooltip.cluster.trend_score.toFixed(1)}</span>
            {tooltip.cluster.velocity && (
              <span
                style={{
                  color:
                    VELOCITY_COLORS[tooltip.cluster.velocity] || "#6B7280",
                }}
              >
                {velocityLabel(tooltip.cluster.velocity)}{" "}
                {tooltip.cluster.velocity}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
