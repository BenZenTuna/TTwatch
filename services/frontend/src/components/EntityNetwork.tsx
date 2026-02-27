"use client";

import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import * as d3 from "d3";
import type {
  EntityNodeResponse,
  EntityEdgeResponse,
  EntityGraphResponse,
} from "@/lib/types";
import { getClusterColor } from "@/lib/design-tokens";
import {
  NetworkNode,
  NetworkLink,
  createNetworkSimulation,
} from "@/lib/force-simulation";

interface EntityNetworkProps {
  graph: EntityGraphResponse;
  onEntityClick?: (entity: EntityNodeResponse) => void;
}

// Map entity types to colors
const ENTITY_TYPE_COLORS: Record<string, string> = {
  person: "#3B82F6",
  organization: "#10B981",
  location: "#F59E0B",
  event: "#EF4444",
  product: "#8B5CF6",
  concept: "#EC4899",
  technology: "#06B6D4",
  org: "#10B981",
};

function getEntityColor(type: string): string {
  return ENTITY_TYPE_COLORS[type.toLowerCase()] || getClusterColor(0);
}

export function EntityNetwork({
  graph,
  onEntityClick,
}: EntityNetworkProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [dimensions, setDimensions] = useState({ width: 600, height: 400 });
  const [filterType, setFilterType] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    entity: EntityNodeResponse;
    connections: number;
  } | null>(null);

  const { entities, edges } = graph;

  // Discover entity types
  const entityTypes = useMemo(() => {
    const types = new Set<string>();
    for (const e of entities) types.add(e.type);
    return Array.from(types).sort();
  }, [entities]);

  // Filter entities and edges by type
  const { filteredEntities, filteredEdges } = useMemo(() => {
    const ents = filterType
      ? entities.filter((e) => e.type === filterType)
      : entities;
    const entIds = new Set(ents.map((e) => e.id));
    const edgs = edges.filter(
      (e) => entIds.has(e.source) && entIds.has(e.target)
    );
    return { filteredEntities: ents, filteredEdges: edgs };
  }, [entities, edges, filterType]);

  // Count connections per entity for tooltip
  const connectionCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of filteredEdges) {
      counts.set(e.source, (counts.get(e.source) || 0) + 1);
      counts.set(e.target, (counts.get(e.target) || 0) + 1);
    }
    return counts;
  }, [filteredEdges]);

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

  const handleEntityClick = useCallback(
    (entity: EntityNodeResponse) => {
      onEntityClick?.(entity);
    },
    [onEntityClick]
  );

  // D3 network
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || filteredEntities.length === 0) return;

    const { width, height } = dimensions;

    // Build nodes
    const maxCount = Math.max(
      ...filteredEntities.map((e) => e.article_count),
      1
    );
    const nodeScale = d3
      .scaleSqrt()
      .domain([1, maxCount])
      .range([8, 30])
      .clamp(true);

    const nodes: NetworkNode[] = filteredEntities.map((e) => ({
      id: e.id,
      name: e.name,
      type: e.type,
      articleCount: e.article_count,
      radius: nodeScale(e.article_count),
    }));

    // Build links from server-computed co-occurrence edges
    const nodeIdSet = new Set(nodes.map((n) => n.id));
    const maxWeight = Math.max(...filteredEdges.map((e) => e.weight), 1);
    const links: NetworkLink[] = filteredEdges
      .filter((e) => nodeIdSet.has(e.source) && nodeIdSet.has(e.target))
      .map((e) => ({
        source: e.source,
        target: e.target,
        sharedCount: e.weight,
      }));

    // Edge width scale
    const widthScale = d3
      .scaleLinear()
      .domain([1, maxWeight])
      .range([1, 5])
      .clamp(true);

    // Edge opacity scale
    const opacityScale = d3
      .scaleLinear()
      .domain([1, maxWeight])
      .range([0.3, 0.8])
      .clamp(true);

    // Build D3 scene
    const svgSelection = d3.select(svg);
    svgSelection.selectAll("*").remove();

    const g = svgSelection.append("g");

    // Zoom
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 4])
      .on("zoom", (event) => {
        g.attr("transform", event.transform.toString());
      });
    svgSelection.call(zoom);

    // Links
    const linkSelection = g
      .selectAll<SVGLineElement, NetworkLink>("line")
      .data(links)
      .join("line")
      .attr("stroke", "#4a5568")
      .attr("stroke-width", (d) => widthScale(d.sharedCount))
      .attr("stroke-opacity", (d) => opacityScale(d.sharedCount));

    // Node groups
    const nodeGroups = g
      .selectAll<SVGGElement, NetworkNode>("g.node")
      .data(nodes, (d) => d.id)
      .join("g")
      .attr("class", "node")
      .style("cursor", "pointer");

    // Drag behavior
    const drag = d3
      .drag<SVGGElement, NetworkNode>()
      .on("start", (event, d) => {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on("drag", (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on("end", (event, d) => {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });

    nodeGroups.call(drag);

    // Circles
    nodeGroups
      .append("circle")
      .attr("r", (d) => d.radius)
      .attr("fill", (d) => getEntityColor(d.type))
      .attr("fill-opacity", 0.7)
      .attr("stroke", (d) => getEntityColor(d.type))
      .attr("stroke-width", 1.5)
      .attr("stroke-opacity", 0.9);

    // Labels (only for nodes with enough room)
    nodeGroups
      .append("text")
      .attr("text-anchor", "middle")
      .attr("dy", (d) => d.radius + 14)
      .attr("fill", "#9ca3af")
      .attr("font-size", "10px")
      .attr("pointer-events", "none")
      .text((d) =>
        d.name.length > 16 ? d.name.slice(0, 14) + "..." : d.name
      );

    // Interactions
    nodeGroups
      .on("mouseenter", function (event: MouseEvent, d: NetworkNode) {
        d3.select(this)
          .select("circle")
          .transition()
          .duration(150)
          .attr("fill-opacity", 0.95)
          .attr("stroke-width", 3);

        // Highlight connected links
        linkSelection
          .attr("stroke-opacity", (l) => {
            const src =
              typeof l.source === "object"
                ? (l.source as NetworkNode).id
                : l.source;
            const tgt =
              typeof l.target === "object"
                ? (l.target as NetworkNode).id
                : l.target;
            return src === d.id || tgt === d.id ? 0.9 : 0.08;
          })
          .attr("stroke", (l) => {
            const src =
              typeof l.source === "object"
                ? (l.source as NetworkNode).id
                : l.source;
            const tgt =
              typeof l.target === "object"
                ? (l.target as NetworkNode).id
                : l.target;
            return src === d.id || tgt === d.id
              ? getEntityColor(d.type)
              : "#4a5568";
          });

        // Dim unconnected nodes
        const connectedIds = new Set<string>();
        connectedIds.add(d.id);
        links.forEach((l) => {
          const src =
            typeof l.source === "object"
              ? (l.source as NetworkNode).id
              : l.source;
          const tgt =
            typeof l.target === "object"
              ? (l.target as NetworkNode).id
              : l.target;
          if (src === d.id) connectedIds.add(String(tgt));
          if (tgt === d.id) connectedIds.add(String(src));
        });
        nodeGroups
          .select("circle")
          .transition()
          .duration(150)
          .attr("fill-opacity", (n: NetworkNode) =>
            connectedIds.has(n.id) ? 0.95 : 0.15
          );
        nodeGroups
          .select("text")
          .transition()
          .duration(150)
          .attr("fill-opacity", (n: NetworkNode) =>
            connectedIds.has(n.id) ? 1 : 0.2
          );

        const entity = filteredEntities.find((e) => e.id === d.id);
        if (entity) {
          const rect = svg.getBoundingClientRect();
          setTooltip({
            x: event.clientX - rect.left,
            y: event.clientY - rect.top - 10,
            entity,
            connections: connectionCounts.get(d.id) || 0,
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
          .attr("stroke-width", 1.5);
        linkSelection
          .attr("stroke-opacity", (d) => opacityScale(d.sharedCount))
          .attr("stroke", "#4a5568");
        nodeGroups
          .select("circle")
          .transition()
          .duration(150)
          .attr("fill-opacity", 0.7);
        nodeGroups
          .select("text")
          .transition()
          .duration(150)
          .attr("fill-opacity", 1);
        setTooltip(null);
      })
      .on("click", function (_event: MouseEvent, d: NetworkNode) {
        const entity = filteredEntities.find((e) => e.id === d.id);
        if (entity) handleEntityClick(entity);
      });

    // Simulation
    const simulation = createNetworkSimulation(nodes, links, width, height);

    simulation.on("tick", () => {
      linkSelection
        .attr("x1", (d) => (d.source as NetworkNode).x || 0)
        .attr("y1", (d) => (d.source as NetworkNode).y || 0)
        .attr("x2", (d) => (d.target as NetworkNode).x || 0)
        .attr("y2", (d) => (d.target as NetworkNode).y || 0);
      nodeGroups.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

    return () => {
      simulation.stop();
    };
  }, [filteredEntities, filteredEdges, dimensions, handleEntityClick, connectionCounts]);

  if (entities.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500 text-sm">
        No entity data available
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative w-full">
      {/* Type filter */}
      <div className="flex flex-wrap gap-2 mb-3">
        <button
          onClick={() => setFilterType(null)}
          className={`text-xs px-2 py-1 rounded border transition-colors ${
            filterType === null
              ? "border-accent text-accent bg-accent/10"
              : "border-surface-border text-gray-400 hover:text-gray-200"
          }`}
        >
          All
        </button>
        {entityTypes.map((type) => {
          const color = getEntityColor(type);
          const active = filterType === type;
          return (
            <button
              key={type}
              onClick={() => setFilterType(active ? null : type)}
              className="text-xs px-2 py-1 rounded border transition-colors"
              style={{
                borderColor: active ? color : "#2a2d3e",
                color: active ? color : "#6b7280",
                backgroundColor: active ? `${color}15` : "transparent",
              }}
            >
              {type}
            </button>
          );
        })}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mb-2">
        {entityTypes
          .filter((t) => !filterType || t === filterType)
          .map((type) => (
            <div key={type} className="flex items-center gap-1.5 text-xs text-gray-500">
              <div
                className="w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: getEntityColor(type) }}
              />
              {type}
            </div>
          ))}
        {edges.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-gray-500">
            <div className="w-4 h-0.5 bg-gray-500 rounded" />
            co-occurrence
          </div>
        )}
      </div>

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
            {tooltip.entity.name}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">
            Type: {tooltip.entity.type}
          </p>
          <p className="text-xs text-gray-400">
            Articles: {tooltip.entity.article_count}
          </p>
          {tooltip.connections > 0 && (
            <p className="text-xs text-gray-400">
              Connections: {tooltip.connections}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
