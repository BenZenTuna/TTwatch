import * as d3 from "d3";

export interface BubbleNode extends d3.SimulationNodeDatum {
  id: string;
  keyword: string;
  articleCount: number;
  color: string;
  trendScore: number;
  velocity: string | null;
  radius: number;
}

const RADIUS_SCALE_RANGE: [number, number] = [20, 80];
const COLLISION_PADDING = 3;
const CENTER_STRENGTH = 0.05;
const CHARGE_STRENGTH = -30;
const VELOCITY_DECAY = 0.3;

export function computeRadius(
  articleCount: number,
  domain: [number, number]
): number {
  const scale = d3
    .scaleSqrt()
    .domain(domain)
    .range(RADIUS_SCALE_RANGE)
    .clamp(true);
  return scale(articleCount);
}

export function createBubbleSimulation(
  nodes: BubbleNode[],
  width: number,
  height: number
): d3.Simulation<BubbleNode, undefined> {
  return d3
    .forceSimulation<BubbleNode>(nodes)
    .velocityDecay(VELOCITY_DECAY)
    .force("center", d3.forceCenter<BubbleNode>(width / 2, height / 2))
    .force(
      "charge",
      d3.forceManyBody<BubbleNode>().strength(CHARGE_STRENGTH)
    )
    .force(
      "collision",
      d3
        .forceCollide<BubbleNode>()
        .radius((d) => d.radius + COLLISION_PADDING)
        .strength(0.8)
    )
    .force(
      "x",
      d3
        .forceX<BubbleNode>(width / 2)
        .strength(CENTER_STRENGTH)
    )
    .force(
      "y",
      d3
        .forceY<BubbleNode>(height / 2)
        .strength(CENTER_STRENGTH)
    );
}

export interface NetworkNode extends d3.SimulationNodeDatum {
  id: string;
  name: string;
  type: string;
  articleCount: number;
  radius: number;
}

export interface NetworkLink extends d3.SimulationLinkDatum<NetworkNode> {
  sharedCount: number;
}

export function createNetworkSimulation(
  nodes: NetworkNode[],
  links: NetworkLink[],
  width: number,
  height: number
): d3.Simulation<NetworkNode, NetworkLink> {
  return d3
    .forceSimulation<NetworkNode>(nodes)
    .velocityDecay(VELOCITY_DECAY)
    .force(
      "link",
      d3
        .forceLink<NetworkNode, NetworkLink>(links)
        .id((d) => d.id)
        .distance(100)
        .strength(0.3)
    )
    .force("center", d3.forceCenter<NetworkNode>(width / 2, height / 2))
    .force(
      "charge",
      d3.forceManyBody<NetworkNode>().strength(-150)
    )
    .force(
      "collision",
      d3
        .forceCollide<NetworkNode>()
        .radius((d) => d.radius + 5)
        .strength(0.9)
    );
}
