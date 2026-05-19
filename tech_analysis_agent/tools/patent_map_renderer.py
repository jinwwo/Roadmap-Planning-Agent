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
    "ASML Holding N.V.": "ASML",
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


def _short_label(value: Any, max_len: int = 24) -> str:
    text = _ascii_label(value)
    if len(text) > max_len:
        text = text[: max_len - 1] + "."
    return "\n".join(textwrap.wrap(text, width=16)) if len(text) > 16 else text


def _node_size(label: str, kind: str) -> int:
    return {"actor": 470, "technology": 500, "industry": 500}.get(kind, 450)


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
    cell_w, cell_h = 2.45, 1.75

    for idx, nodes in enumerate(components):
        row, col = divmod(idx, cols)
        sub = graph.subgraph(nodes)
        if len(nodes) == 1:
            local = {nodes[0]: (0.0, 0.0)}
        elif len(nodes) == 2:
            local = {nodes[0]: (-0.48, 0.0), nodes[1]: (0.48, 0.0)}
        else:
            local = nx.spring_layout(sub, seed=42 + idx, k=0.85, iterations=220)

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
        step = 1.65 / (len(nodes) - 1)
        return {node: 0.825 - i * step for i, node in enumerate(nodes)}

    pos = {node: (-1.05, y) for node, y in y_positions(tech_nodes).items()}
    pos.update({node: (1.05, y) for node, y in y_positions(industry_nodes).items()})
    return pos


def _normalize_view(ax, pos):
    if not pos:
        return
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    x_pad = max(0.42, (max(xs) - min(xs)) * 0.13)
    y_pad = max(0.32, (max(ys) - min(ys)) * 0.16)
    ax.set_xlim(min(xs) - x_pad, max(xs) + x_pad)
    ax.set_ylim(min(ys) - y_pad, max(ys) + y_pad)


def _legend_handles(kinds):
    import matplotlib.lines as mlines

    specs = [
        ("actor", "#2563EB", "Actor"),
        ("technology", "#16A34A", "Technology"),
        ("industry", "#F59E0B", "Industry"),
    ]
    return [
        mlines.Line2D(
            [],
            [],
            color=color,
            marker="o",
            linestyle="None",
            markersize=5.5,
            markerfacecolor="white",
            markeredgewidth=1.5,
            label=label,
        )
        for kind, color, label in specs
        if kind in kinds
    ]


def _draw_node_labels(ax, graph, pos):
    y0, y1 = ax.get_ylim()
    offset = (y1 - y0) * 0.075
    for node, data in graph.nodes(data=True):
        x, y = pos[node]
        label = data.get("label", node)
        kind = data.get("kind")
        va = "bottom"
        y_text = y + offset
        if kind == "industry":
            y_text = y - offset
            va = "top"
        ax.text(
            x,
            y_text,
            label,
            ha="center",
            va=va,
            fontsize=7.1,
            fontweight="semibold",
            color="#111827",
            linespacing=1.03,
            bbox={
                "boxstyle": "round,pad=0.22,rounding_size=0.08",
                "fc": "white",
                "ec": "#E5E7EB",
                "lw": 0.45,
                "alpha": 0.96,
            },
            zorder=5,
        )


def _draw_matrix_map(
    rows: list[str],
    cols: list[str],
    values: Dict[Tuple[str, str], float],
    output_path: Path,
    title: str,
    *,
    row_label: str,
    col_label: str,
) -> bool:
    if not rows or not cols:
        return False

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.facecolor": "#FFFFFF",
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
    })

    matrix = np.array([[values.get((r, c), 0.0) for c in cols] for r in rows], dtype=float)
    width = max(5.8, 1.35 + len(cols) * 1.35 + len(rows) * 0.25)
    height = max(3.5, 1.9 + len(rows) * 0.62)
    fig, ax = plt.subplots(figsize=(width, height), dpi=260)
    ax.set_facecolor("white")

    fig.text(0.055, 0.94, title, ha="left", va="top", fontsize=11.5, fontweight="bold", color="#111827")
    fig.text(
        0.055,
        0.885,
        f"{len(rows)} {row_label.lower()} · {len(cols)} {col_label.lower()} · circle size encodes score",
        ha="left",
        va="top",
        fontsize=7.5,
        color="#6B7280",
    )

    ax.set_xlim(-0.65, len(cols) - 0.35)
    ax.set_ylim(len(rows) - 0.35, -0.65)
    ax.set_xticks(range(len(cols)))
    ax.set_yticks(range(len(rows)))
    ax.set_xticklabels([_short_label(c, max_len=18).replace("\n", " ") for c in cols], fontsize=7.2, fontweight="semibold")
    ax.set_yticklabels([_short_label(r, max_len=18).replace("\n", " ") for r in rows], fontsize=7.2, fontweight="semibold")
    ax.tick_params(axis="both", length=0, colors="#111827")
    ax.xaxis.tick_top()

    for spine in ax.spines.values():
        spine.set_visible(False)

    for x in range(len(cols)):
        ax.axvline(x, color="#F3F4F6", lw=0.8, zorder=0)
    for y in range(len(rows)):
        ax.axhline(y, color="#F3F4F6", lw=0.8, zorder=0)

    xs, ys, sizes, colors = [], [], [], []
    for y, row in enumerate(rows):
        for x, col in enumerate(cols):
            value = matrix[y, x]
            if value <= 0:
                continue
            xs.append(x)
            ys.append(y)
            sizes.append(120 + value * 720)
            colors.append(value)

    scatter = ax.scatter(
        xs,
        ys,
        s=sizes,
        c=colors,
        cmap="Blues",
        vmin=0.0,
        vmax=1.0,
        edgecolors="#1D4ED8",
        linewidths=0.8,
        alpha=0.9,
        zorder=3,
    )

    for y, row in enumerate(rows):
        for x, col in enumerate(cols):
            value = values.get((row, col), 0.0)
            if value > 0:
                ax.text(x, y, f"{value:.2f}", ha="center", va="center", fontsize=5.8, color="white", fontweight="bold", zorder=4)

    cbar = fig.colorbar(scatter, ax=ax, fraction=0.035, pad=0.035)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(labelsize=6.2, length=0, colors="#6B7280")
    cbar.set_label("score", fontsize=6.5, color="#6B7280")

    fig.subplots_adjust(left=0.2, right=0.9, top=0.72, bottom=0.12)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.08, facecolor="white")
    plt.close(fig)
    return True


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
        "axes.facecolor": "#FFFFFF",
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
    })

    width = max(6.4, min(9.8, 5.2 + graph.number_of_nodes() * 0.26))
    height = max(2.75, min(5.8, 2.05 + graph.number_of_nodes() * 0.16))
    fig, ax = plt.subplots(figsize=(width, height), dpi=260)
    ax.axis("off")

    if bipartite:
        pos = _bipartite_positions(graph)
    else:
        pos = _component_layout(graph, nx)

    _normalize_view(ax, pos)

    fig.text(
        0.035,
        0.945,
        title,
        ha="left",
        va="top",
        fontsize=11.5,
        fontweight="bold",
        color="#111827",
    )
    fig.text(
        0.035,
        0.895,
        f"{graph.number_of_nodes()} nodes · {graph.number_of_edges()} links · edge width encodes score",
        ha="left",
        va="top",
        fontsize=7.5,
        color="#6B7280",
    )

    color_by_kind = {
        "actor": "#2563EB",
        "technology": "#16A34A",
        "industry": "#F59E0B",
    }
    node_colors = [
        "white"
        for _, data in graph.nodes(data=True)
    ]
    node_edge_colors = [
        color_by_kind.get(data.get("kind"), "#9CA3AF")
        for _, data in graph.nodes(data=True)
    ]
    node_sizes = [data.get("size", 1350) for _, data in graph.nodes(data=True)]

    edge_weights = [_edge_weight(data.get("weight")) for _, _, data in graph.edges(data=True)]
    edge_widths = [0.75 + w * 2.15 for w in edge_weights]
    edge_alphas = [0.25 + w * 0.42 for w in edge_weights]

    if directed:
        nx.draw_networkx_edges(
            graph,
            pos,
            ax=ax,
            width=edge_widths,
            alpha=0.5,
            edge_color="#9CA3AF",
            arrows=True,
            arrowstyle="-|>",
            arrowsize=11,
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
                edge_color="#9CA3AF",
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
                edge_color="#9CA3AF",
            )

    nx.draw_networkx_nodes(
        graph,
        pos,
        ax=ax,
        node_size=node_sizes,
        node_color=node_colors,
        edgecolors=node_edge_colors,
        linewidths=1.6,
    )
    _draw_node_labels(ax, graph, pos)

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
            font_size=5.7,
            font_color="#374151",
            bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "#E5E7EB", "lw": 0.35, "alpha": 0.94},
        )

    kinds = {data.get("kind") for _, data in graph.nodes(data=True)}
    handles = _legend_handles(kinds)
    if handles:
        fig.legend(
            handles=handles,
            loc="lower left",
            bbox_to_anchor=(0.035, 0.035),
            ncol=min(3, len(handles)),
            frameon=False,
            fontsize=6.8,
        )

    fig.subplots_adjust(left=0.035, right=0.985, top=0.84, bottom=0.13)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.08, facecolor="white")
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


def _technology_industry_matrix(items: list):
    rows = []
    cols = []
    values: Dict[Tuple[str, str], float] = {}
    for item in _top_items(items, "strength", limit=18):
        tech = item.get("tech_id") or item.get("technology")
        if not tech:
            continue
        tech = _short_label(tech, max_len=18).replace("\n", " ")
        if tech not in rows:
            rows.append(tech)
        for industry in item.get("industries") or []:
            industry = _short_label(industry, max_len=18).replace("\n", " ")
            if industry not in cols:
                cols.append(industry)
            values[(tech, industry)] = max(values.get((tech, industry), 0.0), _edge_weight(item.get("strength")))
    return rows, cols, values


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
        path = output_root / f"{safe_prefix}_{filename}"
        if map_key == "technology_industry_map":
            rows, cols, values = _technology_industry_matrix(patent_maps.get(map_key) or [])
            did_render = _draw_matrix_map(
                rows,
                cols,
                values,
                path,
                title,
                row_label="Technologies",
                col_label="Industries",
            )
        else:
            graph = builder(patent_maps.get(map_key) or [])
            did_render = _draw_graph(graph, path, title, directed=directed, bipartite=bipartite)
        if did_render:
            rendered[map_key] = str(path)

    return rendered
