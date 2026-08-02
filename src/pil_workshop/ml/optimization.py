"""Route-optimization model builders using OR-Tools.

Two problems from Section 9:
  * **Empty-container repositioning** — a min-cost flow across ports given
    supply/demand imbalance and per-lane unit costs.
  * **Drayage / last-mile** — a small VRPTW solved with OR-Tools routing.

OR-Tools is imported lazily inside each solver so the module imports cleanly
off-platform; the pure helpers (imbalance shaping, cost matrices) are testable
without it.
"""

from __future__ import annotations

from typing import Any


def build_min_cost_flow(
    supplies: dict[int, int], unit_costs: dict[tuple[int, int], float],
    capacities: dict[tuple[int, int], int] | None = None,
) -> dict[str, Any]:
    """Shape a min-cost-flow problem from port supplies and lane costs.

    ``supplies`` maps node → net balance (positive = surplus of empties,
    negative = deficit). Must sum to zero (we balance it if not). ``unit_costs``
    maps (from, to) → cost per container. Returns a spec dict for
    :func:`solve_min_cost_flow`.
    """
    total = sum(supplies.values())
    supplies = dict(supplies)
    if total != 0:
        # Add a virtual balancing node so total supply nets to zero.
        virtual = max(supplies) + 1 if supplies else 0
        supplies[virtual] = -total
        for (_u, v) in list(unit_costs.keys()):
            unit_costs.setdefault((virtual, v), 0.0)
    arcs = []
    for (u, v), cost in unit_costs.items():
        cap = (capacities or {}).get((u, v), 10_000_000)
        arcs.append({"from": u, "to": v, "capacity": int(cap), "unit_cost": int(round(cost))})
    return {"supplies": supplies, "arcs": arcs}


def solve_min_cost_flow(spec: dict[str, Any]) -> dict[str, Any]:
    """Solve a min-cost flow with OR-Tools; return flows + total cost.

    Returns ``{"status": ..., "total_cost": ..., "flows": [(u,v,flow,cost)]}``.
    """
    from ortools.graph.python import min_cost_flow as mcf

    solver = mcf.SimpleMinCostFlow()
    for a in spec["arcs"]:
        solver.add_arc_with_capacity_and_unit_cost(
            a["from"], a["to"], a["capacity"], a["unit_cost"]
        )
    for node, supply in spec["supplies"].items():
        solver.set_node_supply(node, int(supply))

    status = solver.solve()
    flows = []
    total = 0
    if status == solver.OPTIMAL:
        for i in range(solver.num_arcs()):
            f = solver.flow(i)
            if f > 0:
                cost = f * solver.unit_cost(i)
                total += cost
                flows.append((solver.tail(i), solver.head(i), int(f), int(cost)))
    return {
        "status": "optimal" if status == solver.OPTIMAL else str(status),
        "total_cost": int(total),
        "flows": flows,
    }


def solve_vrptw(
    distance_matrix: list[list[int]],
    time_windows: list[tuple[int, int]],
    num_vehicles: int = 3,
    depot: int = 0,
    service_time: int = 10,
    max_route_time: int = 480,
) -> dict[str, Any]:
    """Solve a small Vehicle-Routing-Problem-with-Time-Windows with OR-Tools.

    Returns per-vehicle routes and the total travel time. Distances double as
    travel times (minutes) for the demo.
    """
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    n = len(distance_matrix)
    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)

    def transit(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return distance_matrix[i][j] + service_time

    transit_idx = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    routing.AddDimension(
        transit_idx, 60, max_route_time, False, "Time"
    )
    time_dim = routing.GetDimensionOrDie("Time")
    for node, (start, end) in enumerate(time_windows):
        if node == depot:
            continue
        index = manager.NodeToIndex(node)
        time_dim.CumulVar(index).SetRange(int(start), int(end))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.FromSeconds(5)

    solution = routing.SolveWithParameters(params)
    if not solution:
        return {"status": "no_solution", "routes": [], "total_time": 0}

    routes = []
    total_time = 0
    for v in range(num_vehicles):
        index = routing.Start(v)
        route = []
        while not routing.IsEnd(index):
            route.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        route.append(manager.IndexToNode(index))
        if len(route) > 2:  # non-trivial route
            arrival = solution.Min(time_dim.CumulVar(routing.End(v)))
            total_time += arrival
            routes.append({"vehicle": v, "stops": route, "route_time": arrival})
    return {"status": "optimal", "routes": routes, "total_time": total_time}
