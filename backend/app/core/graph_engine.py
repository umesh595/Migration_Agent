"""networkx-backed planner core (technique #5). This is the ONLY place migration
order is decided — per-component LLM planning (app/llm) receives each component with
its wave already fixed and never reasons about relative order (Doc 3 §3.3).

Edge semantics: a Dependency(source_id, target_id) means "source_id depends on
target_id" (calls it, reads/writes its data, etc). Migration order must move a
depended-upon component no later than its dependents, so waves are built in
reverse-topological order of the dependency graph.
"""

from __future__ import annotations

import networkx as nx

from app.schemas.architecture import ArchitectureModel
from app.schemas.migration_plan import CoexistenceGroup, Wave


class CapacityExceededError(Exception):
    """Raised when a model exceeds the v1 scale envelope (DECISIONS.md)."""


def _build_graph(model: ArchitectureModel) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(c.id for c in model.components)
    graph.add_edges_from((d.source_id, d.target_id) for d in model.dependencies)
    return graph


def check_scale_envelope(model: ArchitectureModel, max_components: int, max_dependencies: int) -> None:
    if len(model.components) > max_components:
        raise CapacityExceededError(
            f"model has {len(model.components)} components, exceeding the v1 cap of {max_components}. "
            "Split this into subsystem-scoped projects and plan each independently."
        )
    if len(model.dependencies) > max_dependencies:
        raise CapacityExceededError(
            f"model has {len(model.dependencies)} dependencies, exceeding the v1 cap of {max_dependencies}. "
            "Split this into subsystem-scoped projects and plan each independently."
        )


def compute_sequence(model: ArchitectureModel) -> list[Wave]:
    """Returns migration waves in dependency-first order. Components in a mutual-
    dependency cycle are grouped into the same wave (coexistence group) since neither
    can safely move before the other."""

    graph = _build_graph(model)

    sccs = list(nx.strongly_connected_components(graph))
    condensation = nx.condensation(graph, scc=sccs)

    # condensation node -> reverse-topological position (dependency-first)
    order = list(nx.topological_sort(condensation.reverse(copy=True)))

    waves: list[Wave] = []
    for wave_index, scc_node in enumerate(order):
        member_ids = sorted(condensation.nodes[scc_node]["members"])
        coexistence_groups = []
        if len(member_ids) > 1:
            coexistence_groups.append(
                CoexistenceGroup(
                    component_ids=member_ids,
                    reason="mutual dependency cycle — no valid order exists between these components individually",
                    coexistence_strategy=(
                        "migrate together in a single cutover window; if that's infeasible, break the cycle "
                        "first (e.g. introduce an anti-corruption layer or async boundary) before sequencing"
                    ),
                )
            )
            rationale = (
                f"components {member_ids} form a dependency cycle and must move as a unit"
            )
        else:
            component = model.get_component(member_ids[0])
            depends_on = sorted(graph.successors(member_ids[0]))
            rationale = (
                f"'{component.name if component else member_ids[0]}' depends on {depends_on or 'nothing'}; "
                "all of its dependencies are scheduled in this wave or earlier"
            )

        waves.append(
            Wave(
                index=wave_index,
                component_ids=member_ids,
                rationale=rationale,
                coexistence_groups=coexistence_groups,
            )
        )

    return waves


def compute_cross_wave_dependencies(model: ArchitectureModel, waves: list[Wave]) -> list[CoexistenceGroup]:
    """Cut analysis: for every dependency whose two endpoints land in different
    waves, that dependency must keep working across the gap between those two
    cutover events — i.e. it needs temporary coexistence/connectivity. Distinct from
    the cycle-driven groups in compute_sequence (those are same-wave; these are
    cross-wave)."""

    wave_of: dict[str, int] = {cid: w.index for w in waves for cid in w.component_ids}
    groups: list[CoexistenceGroup] = []

    for dep in model.dependencies:
        source_wave = wave_of.get(dep.source_id)
        target_wave = wave_of.get(dep.target_id)
        if source_wave is None or target_wave is None or source_wave == target_wave:
            continue
        span = abs(source_wave - target_wave)
        if span == 0:
            continue
        groups.append(
            CoexistenceGroup(
                component_ids=[dep.source_id, dep.target_id],
                reason=(
                    f"'{dep.source_id}' (wave {source_wave}) depends on '{dep.target_id}' (wave {target_wave}) "
                    f"via a {dep.kind} link that must keep working across {span} intervening wave(s)"
                ),
                coexistence_strategy=(
                    "maintain network/data connectivity between source and target environments for the "
                    "duration between these two waves' cutovers; do not decommission the earlier-migrated "
                    "side's old environment until the later wave completes"
                ),
            )
        )

    return groups


def compute_impact(model: ArchitectureModel, component_id: str) -> dict[str, list[str]]:
    """Reachability-based impact analysis: what depends on this component
    (upstream, would be affected if it changes/moves) and what it depends on
    (downstream)."""

    graph = _build_graph(model)
    if component_id not in graph:
        return {"upstream": [], "downstream": []}

    return {
        "upstream": sorted(nx.ancestors(graph, component_id)),
        "downstream": sorted(nx.descendants(graph, component_id)),
    }


def detect_cycles(model: ArchitectureModel) -> list[list[str]]:
    graph = _build_graph(model)
    return [sorted(scc) for scc in nx.strongly_connected_components(graph) if len(scc) > 1]
