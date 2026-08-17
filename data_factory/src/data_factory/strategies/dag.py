"""Workflow DAG validation.

v1 models workflows as chains: nodes carry a ``position``, edges must form
a single acyclic path (every node has in-degree <= 1 and out-degree <= 1).
Branch/merge parallelism is out of scope for v1; the validator rejects it
explicitly so the executor can rely on a strict linear order.
"""

from __future__ import annotations

from ..meta import models as m


def validate(nodes: list[m.WorkflowNode], edges: list[m.WorkflowEdge]) -> list[str]:
    """Return node ids in execution order; raise on invalid graphs.

    Checks: non-empty nodes; edges reference real nodes; acyclic; chain-only
    (no node has more than one predecessor or successor).
    """
    if not nodes:
        raise ValueError("workflow has no nodes")
    node_ids = {n.id for n in nodes}
    if len(node_ids) != len(nodes):
        raise ValueError("workflow contains duplicate node ids")

    incoming, outgoing = _edge_maps(node_ids, edges)
    _check_chain_arity(incoming, outgoing)
    starts = [nid for nid in node_ids if not incoming[nid]]
    if not starts:
        raise ValueError("workflow contains a cycle (no start node)")
    if len(starts) != 1:
        raise ValueError("workflow must have exactly one start node")

    order = _walk_chain(starts[0], node_ids, outgoing)
    position = {n.id: n.position for n in nodes}
    sorted_order = sorted(order, key=lambda nid: position[nid])
    if sorted_order != order:
        raise ValueError("chain order disagrees with node positions")
    return order


def _edge_maps(node_ids: set[str], edges: list[m.WorkflowEdge]):
    """Build in/out adjacency; raise on edges referencing unknown nodes."""
    incoming: dict[str, list[str]] = {nid: [] for nid in node_ids}
    outgoing: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for edge in edges:
        if edge.from_node not in node_ids or edge.to_node not in node_ids:
            raise ValueError(
                f"edge references unknown node: {edge.from_node} -> {edge.to_node}"
            )
        incoming[edge.to_node].append(edge.from_node)
        outgoing[edge.from_node].append(edge.to_node)
    return incoming, outgoing


def _check_chain_arity(incoming: dict, outgoing: dict) -> None:
    for nid, ins in incoming.items():
        if len(ins) > 1 or len(outgoing[nid]) > 1:
            raise ValueError(
                "v1 workflows are chain-only: node branches or merges are not "
                f"supported ({nid})"
            )


def _walk_chain(start: str, node_ids: set[str], outgoing: dict) -> list[str]:
    order: list[str] = []
    current: str | None = start
    while current is not None:
        order.append(current)
        nxt = outgoing[current]
        current = nxt[0] if nxt else None
    if len(order) != len(node_ids):
        raise ValueError("workflow contains a cycle")
    return order
