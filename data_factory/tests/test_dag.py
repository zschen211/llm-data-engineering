"""DAG validator tests: chain acceptance, cycle/branch rejection."""

import pytest

from data_factory.meta import models as m
from data_factory.strategies import dag


def _node(nid: str, position: int) -> m.WorkflowNode:
    return m.WorkflowNode(id=nid, workflow_id="wf_1", stage_name="s", position=position)


def _edges(pairs) -> list[m.WorkflowEdge]:
    return [
        m.WorkflowEdge(workflow_id="wf_1", from_node=frm, to_node=to)
        for frm, to in pairs
    ]


def test_chain_accepted():
    nodes = [_node("a", 0), _node("b", 1), _node("c", 2)]
    assert dag.validate(nodes, _edges([("a", "b"), ("b", "c")])) == [
        "a",
        "b",
        "c",
    ]


def test_single_node_ok():
    assert dag.validate([_node("a", 0)], []) == ["a"]


def test_empty_rejected():
    with pytest.raises(ValueError):
        dag.validate([], [])


def test_cycle_rejected():
    nodes = [_node("a", 0), _node("b", 1)]
    with pytest.raises(ValueError, match="cycle"):
        dag.validate(nodes, _edges([("a", "b"), ("b", "a")]))


def test_branch_rejected():
    nodes = [_node("a", 0), _node("b", 1), _node("c", 2)]
    with pytest.raises(ValueError, match="chain-only"):
        dag.validate(nodes, _edges([("a", "b"), ("a", "c")]))


def test_merge_rejected():
    nodes = [_node("a", 0), _node("b", 1), _node("c", 2)]
    with pytest.raises(ValueError, match="chain-only"):
        dag.validate(nodes, _edges([("a", "c"), ("b", "c")]))


def test_unknown_node_rejected():
    nodes = [_node("a", 0)]
    with pytest.raises(ValueError, match="unknown node"):
        dag.validate(nodes, _edges([("a", "ghost")]))


def test_multiple_starts_rejected():
    nodes = [_node("a", 0), _node("b", 1)]
    with pytest.raises(ValueError, match="exactly one start"):
        dag.validate(nodes, [])


def test_position_mismatch_rejected():
    nodes = [_node("a", 1), _node("b", 0)]
    with pytest.raises(ValueError, match="positions"):
        dag.validate(nodes, _edges([("a", "b")]))
