"""A small state-graph agent orchestrator.

Nodes are agents (LLM-powered with offline fallback) or tools (database
actions). After each node a router function picks the next node from the
shared state, so the workflow branches: follow-up question, duplicate merge,
new ticket, or polite rejection. Every node appends a trace entry, and the
runner streams them so the UI shows each agent's decision live.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

END = "__end__"


@dataclass
class NodeSpec:
    name: str
    fn: Callable[[dict, object], dict]
    agent: str
    icon: str
    kind: str = "agent"  # "agent" (reasons, may call Gemini) or "tool" (deterministic action)
    description: str = ""


@dataclass
class AgentGraph:
    name: str
    nodes: dict[str, NodeSpec] = field(default_factory=dict)
    edges: dict[str, str] = field(default_factory=dict)
    routers: dict[str, tuple[Callable[[dict], str], dict[str, str]]] = field(default_factory=dict)
    entry: str | None = None

    def add_node(self, name: str, fn, *, agent: str, icon: str, kind: str = "agent", description: str = "") -> None:
        self.nodes[name] = NodeSpec(name, fn, agent, icon, kind, description)
        if self.entry is None:
            self.entry = name

    def add_edge(self, src: str, dst: str) -> None:
        self.edges[src] = dst

    def add_router(self, src: str, router: Callable[[dict], str], routes: dict[str, str]) -> None:
        self.routers[src] = (router, routes)

    def _next(self, node: str, state: dict) -> str:
        if node in self.routers:
            router, routes = self.routers[node]
            key = router(state)
            if key not in routes:
                raise RuntimeError(f"Router after '{node}' returned unknown route '{key}'")
            return routes[key]
        return self.edges.get(node, END)

    def stream(self, state: dict, ctx, max_steps: int = 20) -> Iterator[dict]:
        """Run the graph, yielding an event before and after every node."""
        state.setdefault("trace", [])
        path: list[str] = []
        node = self.entry
        while node != END:
            if len(path) >= max_steps:
                raise RuntimeError("Agent graph exceeded the maximum number of steps")
            spec = self.nodes[node]
            yield {"type": "start", "node": node, "agent": spec.agent, "icon": spec.icon, "kind": spec.kind}
            started = time.perf_counter()
            update = spec.fn(state, ctx) or {}
            elapsed = round((time.perf_counter() - started) * 1000)
            entries = update.pop("trace", [])
            for entry in entries:
                entry.setdefault("node", node)
                entry.setdefault("agent", spec.agent)
                entry.setdefault("icon", spec.icon)
                entry.setdefault("kind", spec.kind)
                entry.setdefault("ms", elapsed)
            state.update(update)
            state["trace"].extend(entries)
            path.append(node)
            for entry in entries:
                yield {"type": "step", **entry}
            node = self._next(node, state)
        state["path"] = path
        yield {"type": "end", "path": path}

    def run(self, state: dict, ctx) -> dict:
        for _ in self.stream(state, ctx):
            pass
        return state

    def describe(self) -> dict:
        """Nodes and edges, for the 'How it works' diagram and docs."""
        edges = [{"from": s, "to": d, "label": ""} for s, d in self.edges.items()]
        for src, (_, routes) in self.routers.items():
            edges.extend({"from": src, "to": dst, "label": key} for key, dst in routes.items())
        return {
            "name": self.name,
            "entry": self.entry,
            "nodes": [
                {"id": n.name, "agent": n.agent, "icon": n.icon, "kind": n.kind, "description": n.description}
                for n in self.nodes.values()
            ],
            "edges": edges,
        }

    def to_mermaid(self) -> str:
        lines = ["flowchart TD", "    START([Citizen message]) --> " + str(self.entry)]
        for n in self.nodes.values():
            shape = f"[{n.icon} {n.agent}]" if n.kind == "agent" else f"[/{n.icon} {n.agent}/]"
            lines.append(f"    {n.name}{shape}")
        for e in self.describe()["edges"]:
            dst = "END([Done])" if e["to"] == END else e["to"]
            label = f"|{e['label']}|" if e["label"] else ""
            lines.append(f"    {e['from']} -->{label} {dst}")
        return "\n".join(lines)
