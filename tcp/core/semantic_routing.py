#!/usr/bin/env python3
"""
Semantic Routing Engine for Distributed TCP Networks
Dr. Marcus Chen - TCP Research Consortium

Advanced semantic routing that adapts to content, performance, and security requirements.
Integrates with Elena's behavioral detection for intelligent path selection.
"""

import asyncio
import hashlib
import heapq
import logging
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import networkx as nx

logger = logging.getLogger(__name__)


class RoutingMetric(IntEnum):
    """Metrics for semantic routing decisions"""

    LATENCY = 0  # Minimize response time
    THROUGHPUT = 1  # Maximize data transfer rate
    SECURITY = 2  # Prioritize secure nodes
    RELIABILITY = 3  # Choose most stable nodes
    LOAD_BALANCE = 4  # Distribute load evenly
    ENERGY_EFFICIENCY = 5  # Minimize power consumption


class ContentType(Enum):
    """Content types for semantic routing optimization"""

    TCP_DESCRIPTOR = "tcp_descriptor"
    CAPABILITY_QUERY = "capability_query"
    BEHAVIORAL_DATA = "behavioral_data"
    SECURITY_ALERT = "security_alert"
    PERFORMANCE_METRICS = "performance_metrics"
    NETWORK_TOPOLOGY = "network_topology"


@dataclass
class RoutingContext:
    """Context information for semantic routing decisions"""

    content_type: ContentType
    data_size_bytes: int
    priority_level: int  # 0=low, 1=normal, 2=high, 3=critical
    security_requirements: Dict[str, Any]
    performance_requirements: Dict[str, float]
    source_node_id: str
    destination_constraints: Dict[str, Any] = field(default_factory=dict)

    # Elena's behavioral context
    behavioral_sensitivity: float = 0.5  # How sensitive to behavioral anomalies
    trust_requirements: float = 0.7  # Minimum trust score required

    # Time constraints
    max_latency_ms: Optional[float] = None
    deadline_timestamp: Optional[float] = None


@dataclass
class SemanticPath:
    """Semantic path with rich metadata"""

    path_id: str
    nodes: List[str]
    total_latency_ms: float
    total_cost: float
    security_score: float
    reliability_score: float
    content_affinity: float  # How well path matches content type

    # Path characteristics
    hop_count: int
    bandwidth_mbps: float
    load_factor: float

    # Dynamic metrics
    current_utilization: float = 0.0
    success_rate: float = 1.0
    last_used: float = 0.0

    # Elena's behavioral metrics
    behavioral_safety_score: float = 1.0
    anomaly_exposure: float = 0.0


class SemanticRoutingGraph:
    """
    Graph representation of network with semantic annotations

    Extends NetworkX with TCP-specific semantic routing capabilities
    """

    def __init__(self):
        self.graph = nx.DiGraph()
        self.node_capabilities: Dict[str, Set[str]] = defaultdict(set)
        self.edge_characteristics: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.content_affinities: Dict[str, Dict[ContentType, float]] = defaultdict(dict)

        # Performance tracking
        self.path_performance_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )
        self.node_load_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )

        # Behavioral integration
        self.behavioral_baselines: Dict[str, Dict[str, float]] = {}
        self.trust_scores: Dict[str, float] = {}
        self.anomaly_scores: Dict[str, float] = {}

    def add_semantic_node(
        self,
        node_id: str,
        capabilities: Set[str],
        content_affinities: Dict[ContentType, float],
        behavioral_baseline: Dict[str, float],
        trust_score: float = 0.8,
    ) -> None:
        """Add node with semantic annotations"""

        # Add to NetworkX graph
        self.graph.add_node(node_id)

        # Store semantic information
        self.node_capabilities[node_id] = capabilities
        self.content_affinities[node_id] = content_affinities
        self.behavioral_baselines[node_id] = behavioral_baseline
        self.trust_scores[node_id] = trust_score
        self.anomaly_scores[node_id] = 0.0

        # Initialize performance tracking
        self.node_load_history[node_id] = deque(maxlen=100)

        logger.debug(
            f"Added semantic node {node_id} with {len(capabilities)} capabilities"
        )

    def add_semantic_edge(
        self,
        source: str,
        destination: str,
        latency_ms: float,
        bandwidth_mbps: float,
        reliability: float,
        security_level: float,
    ) -> None:
        """Add edge with semantic characteristics"""

        # Add to NetworkX graph with weights
        self.graph.add_edge(
            source,
            destination,
            weight=latency_ms,
            bandwidth=bandwidth_mbps,
            reliability=reliability,
            security=security_level,
        )

        # Store detailed characteristics
        self.edge_characteristics[(source, destination)] = {
            "latency_ms": latency_ms,
            "bandwidth_mbps": bandwidth_mbps,
            "reliability": reliability,
            "security_level": security_level,
            "current_load": 0.0,
            "success_rate": 1.0,
            "maintenance_window": None,
        }

    def update_node_behavioral_state(
        self,
        node_id: str,
        trust_score: float,
        anomaly_score: float,
        behavioral_metrics: Dict[str, float],
    ) -> None:
        """Update node's behavioral state for routing decisions"""

        if node_id not in self.graph.nodes:
            return

        self.trust_scores[node_id] = trust_score
        self.anomaly_scores[node_id] = anomaly_score

        # Update behavioral baseline with exponential moving average
        if node_id in self.behavioral_baselines:
            for metric, value in behavioral_metrics.items():
                if metric in self.behavioral_baselines[node_id]:
                    current = self.behavioral_baselines[node_id][metric]
                    # Alpha = 0.1 for conservative updates
                    self.behavioral_baselines[node_id][metric] = (
                        current * 0.9 + value * 0.1
                    )

        logger.debug(
            f"Updated behavioral state for {node_id}: trust={trust_score:.3f}, anomaly={anomaly_score:.3f}"
        )

    def get_content_affinity_score(
        self, node_id: str, content_type: ContentType
    ) -> float:
        """Get node's affinity for specific content type"""

        if node_id in self.content_affinities:
            return self.content_affinities[node_id].get(content_type, 0.5)
        return 0.5  # Default neutral affinity

    def calculate_path_semantic_score(
        self, path: List[str], context: RoutingContext
    ) -> float:
        """Calculate semantic quality score for a path"""

        if len(path) < 2:
            return 0.0

        scores = []

        # Node-based scores
        for node_id in path:
            node_score = 0.0

            # Content affinity
            content_affinity = self.get_content_affinity_score(
                node_id, context.content_type
            )
            node_score += content_affinity * 0.3

            # Trust score (Elena's behavioral requirements)
            trust_score = self.trust_scores.get(node_id, 0.5)
            if trust_score >= context.trust_requirements:
                node_score += trust_score * 0.3
            else:
                node_score *= 0.5  # Penalty for insufficient trust

            # Anomaly sensitivity
            anomaly_score = self.anomaly_scores.get(node_id, 0.0)
            behavioral_penalty = anomaly_score * context.behavioral_sensitivity
            node_score *= 1.0 - behavioral_penalty

            # Load balancing
            current_load = self._get_current_node_load(node_id)
            load_score = 1.0 - current_load
            node_score += load_score * 0.2

            # Capability match
            if context.content_type.value in self.node_capabilities.get(node_id, set()):
                node_score += 0.2  # Bonus for capability match

            scores.append(max(0.0, min(1.0, node_score)))

        # Edge-based scores
        edge_scores = []
        for i in range(len(path) - 1):
            source, dest = path[i], path[i + 1]
            edge_key = (source, dest)

            if edge_key in self.edge_characteristics:
                edge_chars = self.edge_characteristics[edge_key]

                edge_score = 0.0

                # Latency score
                latency = edge_chars["latency_ms"]
                if context.max_latency_ms:
                    if latency <= context.max_latency_ms:
                        edge_score += (1.0 - latency / context.max_latency_ms) * 0.4
                else:
                    edge_score += (
                        max(0.0, 1.0 - latency / 100.0) * 0.4
                    )  # Assume 100ms baseline

                # Reliability score
                edge_score += edge_chars["reliability"] * 0.3

                # Security score
                edge_score += edge_chars["security_level"] * 0.3

                edge_scores.append(max(0.0, min(1.0, edge_score)))
            else:
                edge_scores.append(0.5)  # Default score for unknown edges

        # Combine node and edge scores
        node_average = sum(scores) / len(scores) if scores else 0.0
        edge_average = sum(edge_scores) / len(edge_scores) if edge_scores else 0.0

        return node_average * 0.6 + edge_average * 0.4

    def _get_current_node_load(self, node_id: str) -> float:
        """Get current load for a node"""

        if node_id in self.node_load_history and self.node_load_history[node_id]:
            # Return average of recent load measurements
            recent_loads = list(self.node_load_history[node_id])[
                -10:
            ]  # Last 10 measurements
            return sum(recent_loads) / len(recent_loads)

        return 0.3  # Default moderate load


class SemanticRoutingEngine:
    """
    Advanced semantic routing engine for distributed TCP networks

    Integrates content awareness, performance optimization, and behavioral analysis
    """

    def __init__(self):
        self.routing_graph = SemanticRoutingGraph()
        self.cached_paths: Dict[str, SemanticPath] = {}
        self.routing_strategies: Dict[str, callable] = {}

        # Performance monitoring
        self.route_performance: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=1000)
        )
        self.path_success_rates: Dict[str, float] = defaultdict(lambda: 1.0)

        # Adaptive learning
        self.content_routing_patterns: Dict[
            ContentType, Dict[str, float]
        ] = defaultdict(dict)
        self.behavioral_routing_adjustments: Dict[str, float] = defaultdict(float)

        # Register default routing strategies
        self._register_default_strategies()

        logger.info("Semantic routing engine initialized")

    def add_network_node(
        self,
        node_id: str,
        capabilities: Set[str],
        behavioral_profile: Dict[str, float],
        trust_score: float = 0.8,
    ) -> None:
        """Add node to semantic routing graph"""

        # Determine content affinities based on capabilities
        content_affinities = self._calculate_content_affinities(capabilities)

        self.routing_graph.add_semantic_node(
            node_id=node_id,
            capabilities=capabilities,
            content_affinities=content_affinities,
            behavioral_baseline=behavioral_profile,
            trust_score=trust_score,
        )

        logger.info(f"Added node {node_id} to semantic routing graph")

    def add_network_link(
        self,
        source: str,
        destination: str,
        latency_ms: float,
        bandwidth_mbps: float,
        reliability: float = 0.99,
        security_level: float = 0.8,
    ) -> None:
        """Add network link with characteristics"""

        self.routing_graph.add_semantic_edge(
            source=source,
            destination=destination,
            latency_ms=latency_ms,
            bandwidth_mbps=bandwidth_mbps,
            reliability=reliability,
            security_level=security_level,
        )

    async def find_semantic_path(
        self,
        source: str,
        destination: str,
        context: RoutingContext,
        strategy: str = "default",
    ) -> Optional[SemanticPath]:
        """Find optimal semantic path for given context"""

        # Check cache first
        cache_key = self._generate_cache_key(source, destination, context, strategy)
        if cache_key in self.cached_paths:
            cached_path = self.cached_paths[cache_key]
            if self._is_path_still_valid(cached_path):
                return cached_path

        # Apply routing strategy
        if strategy in self.routing_strategies:
            path_candidates = await self.routing_strategies[strategy](
                source, destination, context
            )
        else:
            path_candidates = await self._default_routing_strategy(
                source, destination, context
            )

        if not path_candidates:
            return None

        # Select best path based on semantic scores
        best_path = self._select_optimal_semantic_path(path_candidates, context)

        if best_path:
            # Cache the result
            self.cached_paths[cache_key] = best_path

            # Record path selection for learning
            await self._record_path_selection(best_path, context)

        return best_path

    async def find_multi_path_routes(
        self, source: str, destination: str, context: RoutingContext, num_paths: int = 3
    ) -> List[SemanticPath]:
        """Find multiple disjoint semantic paths for redundancy"""

        paths = []
        excluded_nodes = set()

        for i in range(num_paths):
            # Find path avoiding previously selected nodes (except endpoints)
            modified_context = context
            if excluded_nodes:
                if "excluded_nodes" not in modified_context.destination_constraints:
                    modified_context.destination_constraints["excluded_nodes"] = set()
                modified_context.destination_constraints["excluded_nodes"].update(
                    excluded_nodes
                )

            path = await self.find_semantic_path(source, destination, modified_context)

            if path:
                paths.append(path)
                # Add intermediate nodes to exclusion list for next iteration
                intermediate_nodes = set(
                    path.nodes[1:-1]
                )  # Exclude source and destination
                excluded_nodes.update(intermediate_nodes)
            else:
                break  # No more disjoint paths available

        return paths

    async def update_behavioral_state(
        self,
        node_id: str,
        trust_score: float,
        anomaly_score: float,
        behavioral_metrics: Dict[str, float],
    ) -> None:
        """Update node behavioral state for routing decisions"""

        self.routing_graph.update_node_behavioral_state(
            node_id, trust_score, anomaly_score, behavioral_metrics
        )

        # Invalidate cached paths that include this node
        await self._invalidate_paths_with_node(node_id)

        # Learn from behavioral changes
        await self._learn_from_behavioral_update(
            node_id, anomaly_score, behavioral_metrics
        )

    async def adaptive_path_optimization(
        self, path_id: str, performance_feedback: Dict[str, float]
    ) -> Optional[SemanticPath]:
        """Adaptively optimize path based on performance feedback"""

        # Find the path being optimized
        target_path = None
        for cached_path in self.cached_paths.values():
            if cached_path.path_id == path_id:
                target_path = cached_path
                break

        if not target_path:
            return None

        # Update path performance metrics
        actual_latency = performance_feedback.get(
            "actual_latency_ms", target_path.total_latency_ms
        )
        success_rate = performance_feedback.get("success_rate", 1.0)

        # Record performance
        self.route_performance[path_id].append(
            {
                "timestamp": time.time(),
                "latency_ms": actual_latency,
                "success_rate": success_rate,
                "predicted_latency": target_path.total_latency_ms,
            }
        )

        # Update path success rate
        self.path_success_rates[path_id] = (
            self.path_success_rates[path_id] * 0.9 + success_rate * 0.1
        )

        # If performance is significantly worse than expected, find alternative
        if actual_latency > target_path.total_latency_ms * 1.5 or success_rate < 0.8:
            logger.info(f"Path {path_id} underperforming, seeking alternative")

            # Create new routing context based on original path
            optimization_context = RoutingContext(
                content_type=ContentType.TCP_DESCRIPTOR,  # Default
                data_size_bytes=1024,  # Estimate
                priority_level=2,  # High priority for optimization
                security_requirements={},
                performance_requirements={
                    "max_latency_ms": target_path.total_latency_ms * 0.8
                },
                source_node_id=target_path.nodes[0],
                behavioral_sensitivity=0.7,  # Higher sensitivity during optimization
                trust_requirements=0.8,
            )

            # Find optimized path
            optimized_path = await self.find_semantic_path(
                target_path.nodes[0], target_path.nodes[-1], optimization_context
            )

            return optimized_path

        return target_path

    def _register_default_strategies(self) -> None:
        """Register default routing strategies"""

        self.routing_strategies = {
            "default": self._default_routing_strategy,
            "performance_optimal": self._performance_optimal_strategy,
            "security_conscious": self._security_conscious_strategy,
            "load_balanced": self._load_balanced_strategy,
            "fault_tolerant": self._fault_tolerant_strategy,
            "behavioral_aware": self._behavioral_aware_strategy,
        }

    async def _default_routing_strategy(
        self, source: str, destination: str, context: RoutingContext
    ) -> List[List[str]]:
        """Default semantic routing strategy"""

        # Use NetworkX shortest path with semantic weights
        try:
            # Calculate semantic weights for edges
            for edge in self.routing_graph.graph.edges():
                weight = self._calculate_semantic_edge_weight(edge[0], edge[1], context)
                self.routing_graph.graph[edge[0]][edge[1]]["semantic_weight"] = weight

            # Find shortest path using semantic weights
            path = nx.shortest_path(
                self.routing_graph.graph, source, destination, weight="semantic_weight"
            )

            return [path]

        except nx.NetworkXNoPath:
            logger.warning(f"No path found from {source} to {destination}")
            return []

    async def _behavioral_aware_strategy(
        self, source: str, destination: str, context: RoutingContext
    ) -> List[List[str]]:
        """Routing strategy that prioritizes behavioral safety"""

        paths = []

        # Filter nodes by trust and anomaly scores
        safe_nodes = set()
        for node_id in self.routing_graph.graph.nodes():
            trust = self.routing_graph.trust_scores.get(node_id, 0.5)
            anomaly = self.routing_graph.anomaly_scores.get(node_id, 0.0)

            behavioral_safety = trust * (1.0 - anomaly * context.behavioral_sensitivity)
            if behavioral_safety >= context.trust_requirements:
                safe_nodes.add(node_id)

        # Create subgraph with only safe nodes
        if source in safe_nodes and destination in safe_nodes:
            safe_subgraph = self.routing_graph.graph.subgraph(safe_nodes)

            try:
                # Find multiple paths in safe subgraph
                for path in nx.shortest_simple_paths(
                    safe_subgraph, source, destination
                ):
                    paths.append(path)
                    if len(paths) >= 3:  # Limit to top 3 paths
                        break
            except nx.NetworkXNoPath:
                pass

        return paths

    def _calculate_content_affinities(
        self, capabilities: Set[str]
    ) -> Dict[ContentType, float]:
        """Calculate content type affinities based on node capabilities"""

        affinities = {}

        # TCP descriptor processing
        tcp_capabilities = {"validate", "transform", "compress", "decompress"}
        tcp_match = len(capabilities & tcp_capabilities) / len(tcp_capabilities)
        affinities[ContentType.TCP_DESCRIPTOR] = tcp_match

        # Capability query processing
        query_capabilities = {"search", "index", "filter", "rank"}
        query_match = len(capabilities & query_capabilities) / len(query_capabilities)
        affinities[ContentType.CAPABILITY_QUERY] = query_match

        # Behavioral data processing
        behavioral_capabilities = {"analyze", "aggregate", "detect", "monitor"}
        behavioral_match = len(capabilities & behavioral_capabilities) / len(
            behavioral_capabilities
        )
        affinities[ContentType.BEHAVIORAL_DATA] = behavioral_match

        # Security alert processing
        security_capabilities = {"encrypt", "decrypt", "audit", "verify"}
        security_match = len(capabilities & security_capabilities) / len(
            security_capabilities
        )
        affinities[ContentType.SECURITY_ALERT] = security_match

        # Performance metrics processing
        performance_capabilities = {"measure", "benchmark", "profile", "optimize"}
        performance_match = len(capabilities & performance_capabilities) / len(
            performance_capabilities
        )
        affinities[ContentType.PERFORMANCE_METRICS] = performance_match

        # Network topology processing
        topology_capabilities = {"route", "discover", "map", "connect"}
        topology_match = len(capabilities & topology_capabilities) / len(
            topology_capabilities
        )
        affinities[ContentType.NETWORK_TOPOLOGY] = topology_match

        return affinities

    def _calculate_semantic_edge_weight(
        self, source: str, destination: str, context: RoutingContext
    ) -> float:
        """Calculate semantic weight for edge based on context"""

        edge_key = (source, destination)
        if edge_key not in self.routing_graph.edge_characteristics:
            return float("inf")  # Unknown edge

        chars = self.routing_graph.edge_characteristics[edge_key]

        # Base weight from latency
        weight = chars["latency_ms"]

        # Adjust for reliability
        reliability_penalty = (1.0 - chars["reliability"]) * 100.0
        weight += reliability_penalty

        # Adjust for security requirements
        if context.security_requirements:
            required_security = context.security_requirements.get(
                "min_security_level", 0.5
            )
            if chars["security_level"] < required_security:
                weight *= 2.0  # Heavy penalty for insufficient security

        # Adjust for current load
        load_penalty = chars["current_load"] * 50.0
        weight += load_penalty

        # Behavioral considerations for destination node
        dest_trust = self.routing_graph.trust_scores.get(destination, 0.5)
        dest_anomaly = self.routing_graph.anomaly_scores.get(destination, 0.0)

        behavioral_penalty = dest_anomaly * context.behavioral_sensitivity * 100.0
        trust_bonus = dest_trust * 10.0  # Bonus for high trust

        weight = weight + behavioral_penalty - trust_bonus

        return max(0.1, weight)  # Ensure positive weight

    def _select_optimal_semantic_path(
        self, path_candidates: List[List[str]], context: RoutingContext
    ) -> Optional[SemanticPath]:
        """Select optimal path from candidates based on semantic scoring"""

        if not path_candidates:
            return None

        scored_paths = []

        for path_nodes in path_candidates:
            # Calculate path metrics
            total_latency = self._calculate_path_latency(path_nodes)
            total_cost = self._calculate_path_cost(path_nodes, context)
            security_score = self._calculate_path_security(path_nodes)
            reliability_score = self._calculate_path_reliability(path_nodes)
            content_affinity = self.routing_graph.calculate_path_semantic_score(
                path_nodes, context
            )

            # Create semantic path object
            path_id = hashlib.md5(
                "->".join(path_nodes).encode(), usedforsecurity=False
            ).hexdigest()[:8]

            semantic_path = SemanticPath(
                path_id=path_id,
                nodes=path_nodes,
                total_latency_ms=total_latency,
                total_cost=total_cost,
                security_score=security_score,
                reliability_score=reliability_score,
                content_affinity=content_affinity,
                hop_count=len(path_nodes) - 1,
                bandwidth_mbps=self._calculate_path_bandwidth(path_nodes),
                load_factor=self._calculate_path_load(path_nodes),
                behavioral_safety_score=self._calculate_behavioral_safety(
                    path_nodes, context
                ),
                anomaly_exposure=self._calculate_anomaly_exposure(path_nodes, context),
            )

            scored_paths.append(semantic_path)

        # Select best path based on weighted scoring
        best_path = max(
            scored_paths, key=lambda p: self._calculate_overall_path_score(p, context)
        )

        return best_path

    def _calculate_overall_path_score(
        self, path: SemanticPath, context: RoutingContext
    ) -> float:
        """Calculate overall path score considering all factors"""

        score = 0.0

        # Latency factor (lower is better)
        latency_score = 1.0 / (1.0 + path.total_latency_ms / 100.0)
        score += latency_score * 0.25

        # Security factor
        score += path.security_score * 0.20

        # Reliability factor
        score += path.reliability_score * 0.20

        # Content affinity factor
        score += path.content_affinity * 0.15

        # Behavioral safety (Elena's requirements)
        score += path.behavioral_safety_score * 0.15

        # Load balancing factor
        load_score = 1.0 / (1.0 + path.load_factor)
        score += load_score * 0.05

        return score

    def _calculate_path_latency(self, path_nodes: List[str]) -> float:
        """Calculate total path latency"""

        total_latency = 0.0

        for i in range(len(path_nodes) - 1):
            edge_key = (path_nodes[i], path_nodes[i + 1])
            if edge_key in self.routing_graph.edge_characteristics:
                total_latency += self.routing_graph.edge_characteristics[edge_key][
                    "latency_ms"
                ]
            else:
                total_latency += 50.0  # Default latency for unknown edges

        return total_latency

    def _calculate_behavioral_safety(
        self, path_nodes: List[str], context: RoutingContext
    ) -> float:
        """Calculate behavioral safety score for path"""

        safety_scores = []

        for node_id in path_nodes:
            trust = self.routing_graph.trust_scores.get(node_id, 0.5)
            anomaly = self.routing_graph.anomaly_scores.get(node_id, 0.0)

            # Node safety = trust * (1 - anomaly_impact)
            anomaly_impact = anomaly * context.behavioral_sensitivity
            node_safety = trust * (1.0 - anomaly_impact)
            safety_scores.append(max(0.0, min(1.0, node_safety)))

        # Return minimum safety score (weakest link)
        return min(safety_scores) if safety_scores else 0.0


async def demonstrate_semantic_routing():
    """Demonstrate semantic routing capabilities"""

    print("🧠 Semantic Routing Engine Demonstration")
    print("=" * 60)
    print("Advanced routing with content awareness and behavioral integration")

    # Initialize routing engine
    routing_engine = SemanticRoutingEngine()

    # Add network nodes with different capabilities
    nodes = [
        (
            "tcp-validator-1",
            {"validate", "transform", "compress"},
            {"tcp_validation_latency": 0.24},
        ),
        (
            "tcp-validator-2",
            {"validate", "optimize", "cache"},
            {"tcp_validation_latency": 0.18},
        ),
        (
            "behavioral-analyzer",
            {"analyze", "detect", "monitor", "aggregate"},
            {"anomaly_detection_rate": 0.02},
        ),
        (
            "security-node",
            {"encrypt", "decrypt", "audit", "verify"},
            {"security_response_time": 8.0},
        ),
        (
            "performance-monitor",
            {"measure", "benchmark", "profile"},
            {"monitoring_latency": 5.0},
        ),
        (
            "load-balancer",
            {"route", "balance", "distribute"},
            {"routing_accuracy": 0.98},
        ),
    ]

    print(f"\n📊 Adding {len(nodes)} specialized nodes:")
    for node_id, capabilities, behavioral_profile in nodes:
        trust_score = 0.9 - (hash(node_id) % 20) / 100.0  # Vary trust scores
        routing_engine.add_network_node(
            node_id, capabilities, behavioral_profile, trust_score
        )
        print(
            f"   {node_id}: {len(capabilities)} capabilities, trust={trust_score:.2f}"
        )

    # Add network links
    links = [
        ("tcp-validator-1", "behavioral-analyzer", 5.0, 1000.0),
        ("tcp-validator-2", "behavioral-analyzer", 3.0, 1500.0),
        ("behavioral-analyzer", "security-node", 8.0, 800.0),
        ("security-node", "performance-monitor", 12.0, 500.0),
        ("load-balancer", "tcp-validator-1", 2.0, 2000.0),
        ("load-balancer", "tcp-validator-2", 4.0, 1800.0),
        ("performance-monitor", "load-balancer", 6.0, 1200.0),
    ]

    print(f"\n🔗 Adding {len(links)} network links:")
    for source, dest, latency, bandwidth in links:
        routing_engine.add_network_link(source, dest, latency, bandwidth)
        print(f"   {source} → {dest}: {latency}ms, {bandwidth}Mbps")

    # Demonstrate semantic routing for different content types
    print(f"\n🧭 Semantic Routing Demonstrations:")

    content_scenarios = [
        (ContentType.TCP_DESCRIPTOR, "load-balancer", "behavioral-analyzer"),
        (ContentType.BEHAVIORAL_DATA, "tcp-validator-1", "security-node"),
        (ContentType.SECURITY_ALERT, "behavioral-analyzer", "performance-monitor"),
    ]

    for content_type, source, destination in content_scenarios:
        print(f"\n   Content: {content_type.value}")
        print(f"   Route: {source} → {destination}")

        # Create routing context
        context = RoutingContext(
            content_type=content_type,
            data_size_bytes=2048,
            priority_level=2,
            security_requirements={"min_security_level": 0.7},
            performance_requirements={"max_latency_ms": 50.0},
            source_node_id=source,
            behavioral_sensitivity=0.6,
            trust_requirements=0.75,
        )

        # Find optimal path
        optimal_path = await routing_engine.find_semantic_path(
            source, destination, context
        )

        if optimal_path:
            print(f"   Optimal path: {' → '.join(optimal_path.nodes)}")
            print(f"   Total latency: {optimal_path.total_latency_ms:.1f}ms")
            print(f"   Security score: {optimal_path.security_score:.2f}")
            print(f"   Content affinity: {optimal_path.content_affinity:.2f}")
            print(f"   Behavioral safety: {optimal_path.behavioral_safety_score:.2f}")
        else:
            print(f"   No suitable path found")

    # Demonstrate behavioral adaptation
    print(f"\n🚨 Behavioral Adaptation Demonstration:")

    # Simulate anomaly detection on a node
    anomalous_node = "tcp-validator-1"
    await routing_engine.update_behavioral_state(
        anomalous_node,
        trust_score=0.4,  # Reduced trust
        anomaly_score=0.8,  # High anomaly
        behavioral_metrics={"tcp_validation_latency": 2.5, "error_rate": 0.15},
    )

    print(f"   Anomaly detected on {anomalous_node}")
    print(f"   Trust score: 0.4, Anomaly score: 0.8")

    # Find new path avoiding anomalous node
    adapted_context = RoutingContext(
        content_type=ContentType.TCP_DESCRIPTOR,
        data_size_bytes=1024,
        priority_level=3,  # Critical priority
        security_requirements={"min_security_level": 0.8},
        performance_requirements={},
        source_node_id="load-balancer",
        behavioral_sensitivity=0.9,  # High sensitivity to anomalies
        trust_requirements=0.8,
    )

    adapted_path = await routing_engine.find_semantic_path(
        "load-balancer", "performance-monitor", adapted_context, "behavioral_aware"
    )

    if adapted_path:
        print(f"   Adapted path: {' → '.join(adapted_path.nodes)}")
        print(f"   Avoids anomalous node: {anomalous_node not in adapted_path.nodes}")
        print(f"   Behavioral safety: {adapted_path.behavioral_safety_score:.2f}")

    print(f"\n✅ Semantic Routing Engine: OPERATIONAL")
    print(
        f"   Features: Content-aware routing, behavioral integration, adaptive optimization"
    )
    print(f"   Integration: Elena's behavioral detection, performance optimization")


if __name__ == "__main__":
    asyncio.run(demonstrate_semantic_routing())
