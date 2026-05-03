"""
tools/patent_map_renderer.py
────────────────────────────
Render Patent Agent `patent_maps` into publication-style PNG artifacts.

LLMs produce structured map JSON. This module deterministically turns that
JSON into compact, English-labeled graph figures for human inspection.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


ACTOR_ALIASES = {
    "Taiwan Semiconductor Manufacturing Company": "TSMC",
    "Taiwan Semiconductor Manufacturing Co": "TSMC",
    "Samsung Electronics Co., Ltd.": "Samsung",
    "Samsung Electronics": "Samsung",
    "Intel Corporation": "Intel",
    "ASML Netherlands B.V.": "ASML",
    "Applied Materials, Inc.": "Applied Materials",
    "Tokyo Electron Limited": "TEL",
    "Lam Research Corporation": "Lam Research",
    "SK hynix Inc.": "SK hynix",
    "IBM Corporation": "IBM",
    "GlobalFoundries Inc.": "GlobalFoundries",
}

INDUSTRY_ALIASES = {
    "AI 가속기": "AI Accelerator",
    "AI accelerator": "AI Accelerator",
    "Foundry": "Foundry",
    "Advanced Packaging": "Advanced Packaging",
    "Memory": "Memory",
    "Semiconductor Equipment": "Semiconductor Equipment",
    "Materials and Chemicals": "Materials & Chemicals",
}


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "patent_map"


def _to_float(value: Any, default: float = 0.5) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _edge_weight(value: Any) -> float:
    return max(0.15, min(1.0, _to_float(value, 0.5)))


def _ascii_label(value: Any) -> str:
    text = str(value or "").strip()
    text = ACTOR_ALIASES.get(text, INDUSTRY_ALIASES.get(text, text))
    text = re.sub(r"\b(Co\.?,?\s*Ltd\.?|Corporation|Limited|Inc\.?|B\.V\.)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    text = text.encode("ascii", "ignore").decode("ascii").strip()
    return text or "N/A"


def _short_label(value: Any, max_len: int = 22) -> str:
    text = _ascii_label(value)
    if len(text) > max_len:
        text = text[: max_len - 1] + "."
    return "\n".join(textwrap.wrap(text, width=13)) if len(text) > 13 else text


def _node_size(label: str, kind: str) -> int:
    base = {"actor": 1550, "technology": 1500, "industry": 1450}.get(kind, 1350)
    return base + min(450, len(label.replace("\n", "")) * 12)


def _add_node(graph, node_id: str, label: str, kind: str) -> None:
    clean_label = _short_label(label)
    graph.add_node(node_id, label=clean_label, kind=kind, size=_node_size(clean_label, kind))


def _top_items(items: Iterable[dict], key: str, limit: int = 20) -> list:
    return sorted(items or [], key=lambda x: _to_float(x.get(key)), reverse=True)[:limit]


def _component_layout(graph, nx):
    """Pack disconnected components into a compact grid instead of canvas corners."""
    if graph.number_of_nodes() == 1:
        return {next(iter(graph.nodes())): (0.0, 0.0)}

    components = [list(c) for c in nx.connected_components(graph.to_undirected())]
    components.sort(key=len, reverse=True)
    cols = 2 if len(components) > 1 else 1
    pos = {}
    cell_w, cell_h = 3.0, 2.35

    for idx, nodes in enumerate(components):
        row, col = divmod(idx, cols)
        sub = graph.subgraph(nodes)
        if len(nodes) == 1:
            local = {nodes[0]: (0.0, 0.0)}
        elif len(nodes) == 2:
            local = {nodes[0]: (-0.55, 0.0), nodes[1]: (0.55, 0.0)}
        else:
            local = nx.spring_layout(sub, seed=42 + idx, k=1.2, iterations=200)

        x_offset = (col - (cols - 1) / 2) * cell_w
        y_offset = -row * cell_h
        for node, (x, y) in local.items():
            pos[node] = (float(x) + x_offset, float(y) + y_offset)
    return pos


def _bipartite_positions(graph):
    """Stable two-column layout for technology-industry maps."""
    tech_nodes = sorted(
        [n for n, d in graph.nodes(data=True) if d.get("kind") == "technology"],
        key=lambda n: graph.nodes[n].get("label", n),
    )
    industry_nodes = sorted(
        [n for n, d in graph.nodes(data=True) if d.get("kind") == "industry"],
        key=lambda n: graph.nodes[n].get("label", n),
    )

    def y_positions(nodes):
        if not nodes:
            return {}
        if len(nodes) == 1:
            return {nodes[0]: 0.0}
        step = 2.2 / (len(nodes) - 1)
        return {node: 1.1 - i * step for i, node in enumerate(nodes)}

    pos = {node: (-1.15, y) for node, y in y_positions(tech_nodes).items()}
    pos.update({node: (1.15, y) for node, y in y_positions(industry_nodes).items()})
    return pos


def _normalize_view(ax, pos):
    if not pos:
        return
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    x_pad = max(0.55, (max(xs) - min(xs)) * 0.18)
    y_pad = max(0.45, (max(ys) - min(ys)) * 0.22)
    ax.set_xlim(min(xs) - x_pad, max(xs) + x_pad)
    ax.set_ylim(min(ys) - y_pad, max(ys) + y_pad)


def _legend_handles(kinds):
    import matplotlib.patches as mpatches

    specs = [
        ("actor", "#4C78A8", "Actor"),
        ("technology", "#59A14F", "Technology"),
        ("industry", "#F2C14E", "Industry"),
    ]
    return [mpatches.Patch(color=color, label=label) for kind, color, label in specs if kind in kinds]


def _draw_graph(
    graph,
    output_path: Path,
    title: str,
    *,
    directed: bool = False,
    bipartite: bool = False,
) -> bool:
    if graph.number_of_nodes() == 0:
        return False

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.facecolor": "#FAFAFA",
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
    })

    width = max(7.4, min(11.5, 5.8 + graph.number_of_nodes() * 0.36))
    height = max(5.2, min(8.2, 4.3 + graph.number_of_nodes() * 0.22))
    fig, ax = plt.subplots(figsize=(width, height), dpi=220)
    ax.axis("off")

    if bipartite:
        pos = _bipartite_positions(graph)
    else:
        pos = _component_layout(graph, nx)

    ax.text(
        0.0,
        1.055,
        title,
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=13,
        fontweight="bold",
        color="#222222",
    )
    ax.text(
        0.0,
        1.018,
        f"{graph.number_of_nodes()} nodes · {graph.number_of_edges()} links · edge width encodes score",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=8.5,
        color="#666666",
    )

    color_by_kind = {
        "actor": "#4C78A8",
        "technology": "#59A14F",
        "industry": "#F2C14E",
    }
    node_colors = [
        color_by_kind.get(data.get("kind"), "#A6A6A6")
        for _, data in graph.nodes(data=True)
    ]
    node_sizes = [data.get("size", 1350) for _, data in graph.nodes(data=True)]

    edge_weights = [_edge_weight(data.get("weight")) for _, _, data in graph.edges(data=True)]
    edge_widths = [0.8 + w * 2.4 for w in edge_weights]
    edge_alphas = [0.28 + w * 0.45 for w in edge_weights]

    if directed:
        nx.draw_networkx_edges(
            graph,
            pos,
            ax=ax,
            width=edge_widths,
            alpha=0.58,
            edge_color="#5F6368",
            arrows=True,
            arrowstyle="-|>",
            arrowsize=13,
            connectionstyle="arc3,rad=0.12",
        )
    elif bipartite:
        edge_list = list(graph.edges())
        for idx, (edge, alpha, width) in enumerate(zip(edge_list, edge_alphas, edge_widths)):
            rad = 0.14 if idx % 2 == 0 else -0.14
            nx.draw_networkx_edges(
                graph,
                pos,
                ax=ax,
                edgelist=[edge],
                width=width,
                alpha=alpha,
                edge_color="#5F6368",
                arrows=True,
                arrowstyle="-",
                arrowsize=1,
                connectionstyle=f"arc3,rad={rad}",
            )
    else:
        for (edge, alpha, width) in zip(graph.edges(), edge_alphas, edge_widths):
            nx.draw_networkx_edges(
                graph,
                pos,
                ax=ax,
                edgelist=[edge],
                width=width,
                alpha=alpha,
                edge_color="#5F6368",
            )

    nx.draw_networkx_nodes(
        graph,
        pos,
        ax=ax,
        node_size=node_sizes,
        node_color=node_colors,
        edgecolors="#2B2B2B",
        linewidths=0.7,
    )
    nx.draw_networkx_labels(
        graph,
        pos,
        labels={n: data.get("label", n) for n, data in graph.nodes(data=True)},
        ax=ax,
        font_size=6.2,
        font_weight="bold",
        font_color="#202124",
    )

    if (not bipartite) and graph.number_of_edges() <= 12:
        edge_labels = {
            (u, v): f"{_edge_weight(data.get('weight')):.2f}"
            for u, v, data in graph.edges(data=True)
        }
        nx.draw_networkx_edge_labels(
            graph,
            pos,
            edge_labels=edge_labels,
            ax=ax,
            font_size=6,
            font_color="#444444",
            bbox={"boxstyle": "round,pad=0.13", "fc": "white", "ec": "#DDDDDD", "lw": 0.35, "alpha": 0.9},
        )

    kinds = {data.get("kind") for _, data in graph.nodes(data=True)}
    handles = _legend_handles(kinds)
    if handles:
        ax.legend(
            handles=handles,
            loc="lower left",
            bbox_to_anchor=(0.0, -0.02),
            ncol=min(3, len(handles)),
            frameon=False,
            fontsize=7.2,
        )

    _normalize_view(ax, pos)
    fig.tight_layout(pad=1.6)
    fig.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return True


def _actor_similarity_graph(items: list):
    import networkx as nx

    graph = nx.Graph()
    for item in _top_items(items, "similarity", limit=18):
        a = item.get("actor_a")
        b = item.get("actor_b")
        if not a or not b:
            continue
        _add_node(graph, f"actor:{a}", a, "actor")
        _add_node(graph, f"actor:{b}", b, "actor")
        graph.add_edge(f"actor:{a}", f"actor:{b}", weight=_edge_weight(item.get("similarity")))
    return graph


def _actor_relations_graph(items: list):
    import networkx as nx

    graph = nx.DiGraph()
    for item in _top_items(items, "strength", limit=18):
        source = item.get("source_actor")
        target = item.get("target_actor")
        if not source or not target:
            continue
        _add_node(graph, f"actor:{source}", source, "actor")
        _add_node(graph, f"actor:{target}", target, "actor")
        graph.add_edge(f"actor:{source}", f"actor:{target}", weight=_edge_weight(item.get("strength")))
    return graph


def _technology_industry_graph(items: list):
    import networkx as nx

    graph = nx.Graph()
    for item in _top_items(items, "strength", limit=18):
        tech_id = item.get("tech_id") or item.get("technology")
        tech_label = item.get("tech_id") or item.get("technology")
        if not tech_id:
            continue
        tech_node = f"tech:{tech_id}"
        _add_node(graph, tech_node, tech_label, "technology")
        for industry in (item.get("industries") or [])[:4]:
            industry_node = f"industry:{industry}"
            _add_node(graph, industry_node, industry, "industry")
            graph.add_edge(tech_node, industry_node, weight=_edge_weight(item.get("strength")))
    return graph


def _technology_affinity_graph(items: list):
    import networkx as nx

    graph = nx.Graph()
    for item in _top_items(items, "affinity", limit=20):
        a = item.get("tech_a")
        b = item.get("tech_b")
        if not a or not b:
            continue
        _add_node(graph, f"tech:{a}", a, "technology")
        _add_node(graph, f"tech:{b}", b, "technology")
        graph.add_edge(f"tech:{a}", f"tech:{b}", weight=_edge_weight(item.get("affinity")))
    return graph


def render_patent_maps(
    patent_maps: Dict[str, Any],
    output_dir: str,
    prefix: str = "",
) -> Dict[str, str]:
    """Render patent maps into PNG files and return absolute file paths."""
    if not patent_maps:
        return {}

    try:
        import matplotlib  # noqa: F401
        import networkx  # noqa: F401
    except ImportError as exc:
        print(f"[Patent Map Renderer] skip: missing optional dependency ({exc})")
        return {}

    output_root = Path(output_dir)
    safe_prefix = _safe_name(prefix)
    rendered: Dict[str, str] = {}

    specs: Tuple[Tuple[str, str, Any, str, bool, bool], ...] = (
        (
            "actor_similarity_map",
            "Actor Similarity Map",
            _actor_similarity_graph,
            "actor_similarity_map.png",
            False,
            False,
        ),
        (
            "actor_relations_map",
            "Actor Relations Map",
            _actor_relations_graph,
            "actor_relations_map.png",
            True,
            False,
        ),
        (
            "technology_industry_map",
            "Technology-Industry Map",
            _technology_industry_graph,
            "technology_industry_map.png",
            False,
            True,
        ),
        (
            "technology_affinity_map",
            "Technology Affinity Map",
            _technology_affinity_graph,
            "technology_affinity_map.png",
            False,
            False,
        ),
    )

    for map_key, title, builder, filename, directed, bipartite in specs:
        graph = builder(patent_maps.get(map_key) or [])
        path = output_root / f"{safe_prefix}_{filename}"
        if _draw_graph(graph, path, title, directed=directed, bipartite=bipartite):
            rendered[map_key] = str(path)

    return rendered
