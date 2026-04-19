"""apply-edits — round-trip an edited HTML back into PSD / SVG / HTML / PNG.

Reads an edited LongcatDesign HTML (typically the file downloaded from the
browser's ⬇️ Save button), extracts per-layer state from the `data-*` attrs
and inline images, re-renders text layers from scratch via `render_text_layer`,
recomposites, and writes a new run_dir + trajectory with `metadata.
parent_run_id` pointing back at the source run.

Key properties:
- The edited HTML is the authoritative source — the original run_dir is not
  required. Background PNG is decoded from its `data:image/*;base64,...` URI.
- Text layer PNGs are re-rendered from the data-* attrs (bbox, font_size_px,
  fill, font_family, align) plus the text content inside the .layer.text div.
- Empty text layers are skipped with a warning. Layer ordering follows the
  DOM (which is authored in z_index-ascending order by html_renderer.py).
- The new trajectory has an empty agent_trace / critique_loop — this was not
  an agent run. `metadata.source = "apply-edits"` flags it for downstream
  tooling. See DECISIONS.md for the rationale.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from .config import Settings, load_settings
from .schema import (
    ArtifactType, CompositionArtifacts, DesignSpec, LayerNode, SafeZone,
    TextEffect, Trajectory,
)
from .tools import ToolContext
from .tools.composite import composite
from .tools.render_text_layer import render_text_layer
from .util.ids import new_run_id
from .util.io import sha256_file
from .util.logging import log


# --- public entry ---------------------------------------------------------


def apply_edits(
    edited_html: Path,
    *,
    settings: Settings | None = None,
    out_dir: Path | None = None,
) -> tuple[Trajectory, Path]:
    """Apply edits from `edited_html` — return (Trajectory, trajectory_path).

    Side-effects: creates `out/runs/<new_run_id>/` with poster.{psd,svg,html,
    preview.png} + `out/trajectories/<new_run_id>.json`.
    """
    if not edited_html.exists():
        raise FileNotFoundError(f"edited HTML not found: {edited_html}")

    settings = settings or load_settings()
    doc = BeautifulSoup(edited_html.read_text(encoding="utf-8"), "html.parser")

    parent_run_id = _meta_content(doc, "ld-run-id") or None
    title = doc.title.get_text(strip=True) if doc.title else ""

    new_id = new_run_id()
    run_dir = out_dir or settings.out_dir / "runs" / new_id
    layers_dir = run_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    ctx = ToolContext(settings=settings, run_dir=run_dir,
                      layers_dir=layers_dir, run_id=new_id)

    # Detect artifact mode — landing has <main class="ld-landing">,
    # poster has <div class="canvas">.
    landing_main = doc.find("main", class_="ld-landing")
    poster_canvas = doc.find("div", class_="canvas")

    skipped: list[str] = []

    if landing_main is not None:
        cw = int(landing_main.get("data-w") or 1200)
        ctx.state["design_spec"] = _stub_design_spec(title, cw, 2400,
                                                     ArtifactType.LANDING)
        layer_graph = _restore_landing(landing_main, ctx, skipped)
        # Landing doesn't populate rendered_layers; it stores the tree on
        # design_spec.layer_graph for composite to walk.
        ctx.state["design_spec"] = ctx.state["design_spec"].model_copy(
            update={"layer_graph": layer_graph}
        )
        log("apply.landing.restored",
            sections=sum(1 for n in layer_graph if n.kind == "section"),
            text_layers=sum(
                1 for s in layer_graph for c in (s.children or [])
                if c.kind == "text"
            ),
            skipped=len(skipped),
            parent=parent_run_id or "(none)")
    elif poster_canvas is not None:
        cw = int(poster_canvas.get("data-w") or 0)
        ch = int(poster_canvas.get("data-h") or 0)
        if not (cw > 0 and ch > 0):
            raise ValueError(f"canvas missing data-w/data-h in {edited_html}")
        ctx.state["design_spec"] = _stub_design_spec(title, cw, ch,
                                                     ArtifactType.POSTER)
        for layer_div in poster_canvas.select(".layer"):
            assert isinstance(layer_div, Tag)
            action = _restore_layer(layer_div, cw, ch, ctx)
            if action == "skipped":
                skipped.append(layer_div.get("data-layer-id", "?"))
        if not ctx.state["rendered_layers"]:
            raise RuntimeError(
                f"no layers recovered from {edited_html} "
                "(is the HTML an actual LongcatDesign output?)"
            )
        log("apply.poster.restored",
            count=len(ctx.state["rendered_layers"]),
            skipped=len(skipped),
            parent=parent_run_id or "(none)")
    else:
        raise ValueError(
            f"neither a poster `.canvas` nor a landing `.ld-landing` container "
            f"found in {edited_html} — is this a LongcatDesign HTML?"
        )

    obs = composite({}, ctx=ctx)
    if obs.status != "ok":
        raise RuntimeError(f"composite failed: {obs.summary}")

    traj = _build_trajectory(ctx, parent_run_id, edited_html, skipped, title)
    traj_path = settings.out_dir / "trajectories" / f"{new_id}.json"
    traj_path.parent.mkdir(parents=True, exist_ok=True)
    traj_path.write_text(
        json.dumps(traj.model_dump(mode="json"), ensure_ascii=False, indent=2,
                   default=str),
        encoding="utf-8",
    )
    log("apply.done", run_id=new_id, parent=parent_run_id or "(none)",
        traj=str(traj_path), layers=len(traj.layer_graph))

    return traj, traj_path


# --- layer restoration ----------------------------------------------------


def _restore_layer(div: Tag, cw: int, ch: int, ctx: ToolContext) -> str:
    kind = div.get("data-kind")
    layer_id = div.get("data-layer-id")
    if not (kind and layer_id):
        return "skipped"

    z_index = _int_attr(div, "data-z-index", 0)
    name = div.get("data-layer-name") or layer_id

    if kind == "background":
        return _restore_image(div, layer_id, name, kind, z_index,
                              ctx, full_canvas=(cw, ch))
    if kind == "brand_asset":
        return _restore_image(div, layer_id, name, kind, z_index,
                              ctx, full_canvas=None)
    if kind == "text":
        return _restore_text(div, layer_id, name, z_index, ctx)

    log("apply.skip", reason=f"unknown kind={kind!r}", layer_id=layer_id)
    return "skipped"


def _restore_text(div: Tag, layer_id: str, name: str, z_index: int,
                  ctx: ToolContext) -> str:
    # Strip the drag-handle span before reading text — downloaded HTML
    # already omits it, but re-running apply-edits on a non-downloaded source
    # should still work.
    for span in div.find_all(class_="ld-drag-handle"):
        span.decompose()
    text = div.get_text(strip=False).strip()
    if not text:
        log("apply.skip", reason="empty text", layer_id=layer_id)
        return "skipped"

    bbox = {
        "x": _int_attr(div, "data-bbox-x", 0),
        "y": _int_attr(div, "data-bbox-y", 0),
        "w": _int_attr(div, "data-bbox-w", 0),
        "h": _int_attr(div, "data-bbox-h", 0),
    }
    if not (bbox["w"] > 0 and bbox["h"] > 0):
        log("apply.skip", reason="zero-size bbox", layer_id=layer_id, bbox=bbox)
        return "skipped"

    font_size_px = _int_attr(div, "data-font-size-px",
                             ctx.settings.fonts and 64 or 64)
    font_family = div.get("data-font-family") or ctx.settings.default_text_font
    fill = div.get("data-fill") or "#000000"
    align = div.get("data-align") or "left"

    obs = render_text_layer({
        "layer_id": layer_id,
        "name": name,
        "text": text,
        "font_family": font_family,
        "font_size_px": font_size_px,
        "fill": fill,
        "bbox": bbox,
        "align": align,
        "z_index": z_index,
    }, ctx=ctx)
    if obs.status not in ("ok", "partial"):
        log("apply.error", layer_id=layer_id, summary=obs.summary)
        return "skipped"
    return "ok"


def _restore_image(div: Tag, layer_id: str, name: str, kind: str,
                   z_index: int, ctx: ToolContext,
                   *, full_canvas: tuple[int, int] | None) -> str:
    img = div.find("img")
    if img is None or not img.get("src", "").startswith("data:image/"):
        log("apply.skip", reason="image has no data: URI", layer_id=layer_id)
        return "skipped"

    mime, b64_data = _parse_data_uri(img["src"])
    ext_map = {"image/png": "png", "image/jpeg": "jpg",
               "image/webp": "webp", "image/gif": "gif"}
    ext = ext_map.get(mime, "png")
    prefix = "bg" if kind == "background" else "asset"
    out_path = ctx.layers_dir / f"{prefix}_{layer_id}.{ext}"
    out_path.write_bytes(base64.b64decode(b64_data))

    if full_canvas is not None:
        cw, ch = full_canvas
        bbox = {"x": 0, "y": 0, "w": cw, "h": ch}
    else:
        bbox = {
            "x": _int_attr(div, "data-bbox-x", 0),
            "y": _int_attr(div, "data-bbox-y", 0),
            "w": _int_attr(div, "data-bbox-w", 0),
            "h": _int_attr(div, "data-bbox-h", 0),
        }

    ctx.state["rendered_layers"][layer_id] = {
        "layer_id": layer_id,
        "name": name,
        "kind": kind,
        "z_index": z_index,
        "bbox": bbox,
        "src_path": str(out_path),
        "sha256": sha256_file(out_path),
    }
    return "ok"


# --- trajectory assembly --------------------------------------------------


def _stub_design_spec(title: str, cw: int, ch: int,
                      artifact_type: ArtifactType = ArtifactType.POSTER) -> DesignSpec:
    """Minimal spec — canvas is the only field render_text_layer touches
    (poster path). Landing path reads layer_graph directly."""
    return DesignSpec(
        brief=(title or "(restored from edited HTML)")[:500],
        artifact_type=artifact_type,
        canvas={"w_px": cw, "h_px": ch, "dpi": 96 if artifact_type == ArtifactType.LANDING else 300,
                "aspect_ratio": f"{cw}:{ch}", "color_mode": "RGB"},
        palette=[],
        typography={},
        mood=[],
        composition_notes="Restored from an edited HTML via apply-edits.",
        layer_graph=[],
    )


def _restore_landing(main_el: Tag, ctx: ToolContext,
                     skipped: list[str]) -> list[LayerNode]:
    """Walk a `<main class='ld-landing'>` tree and return a list[LayerNode]
    for design_spec.layer_graph. Each <section> becomes a kind='section'
    node with children = text layers inside."""
    out: list[LayerNode] = []
    for section in main_el.find_all("section", class_="ld-section", recursive=False):
        s_layer_id = section.get("data-layer-id") or ""
        s_name = section.get("data-layer-name") or "content"
        s_z = _int_attr(section, "data-z-index", len(out) + 1)

        children: list[LayerNode] = []
        for div in section.find_all("div", class_="layer", recursive=False):
            text_node = _landing_text_from_div(div, skipped)
            if text_node is not None:
                children.append(text_node)

        out.append(LayerNode(
            layer_id=s_layer_id or f"S{len(out) + 1}",
            name=s_name,
            kind="section",
            z_index=s_z,
            bbox=None,
            children=children,
        ))
    return out


def _landing_text_from_div(div: Tag, skipped: list[str]) -> LayerNode | None:
    kind = div.get("data-kind")
    if kind != "text":
        return None
    layer_id = div.get("data-layer-id") or ""
    # Strip the drag-handle span defensively (shouldn't be there for landing,
    # but round-tripped HTMLs might).
    for span in div.find_all(class_="ld-drag-handle"):
        span.decompose()
    text = div.get_text(strip=False).strip()
    if not text:
        skipped.append(layer_id or "?")
        return None
    fill_raw = div.get("data-fill") or ""
    return LayerNode(
        layer_id=layer_id or f"L-{id(div)}",
        name=div.get("data-layer-name") or layer_id,
        kind="text",
        z_index=_int_attr(div, "data-z-index", 1),
        bbox=None,
        text=text,
        font_family=div.get("data-font-family") or None,
        font_size_px=_int_attr(div, "data-font-size-px", 40) or None,
        align=div.get("data-align") or None,
        effects=TextEffect(fill=fill_raw) if fill_raw else None,
    )


def _build_trajectory(ctx: ToolContext, parent_run_id: str | None,
                      source_html: Path, skipped: list[str],
                      title: str) -> Trajectory:
    # Landing keeps its layer_graph on design_spec (the section tree);
    # poster rebuilds it from rendered_layers (the flat blackboard).
    spec = ctx.state["design_spec"]
    if spec.artifact_type == ArtifactType.LANDING:
        layer_graph = list(spec.layer_graph or [])
    else:
        layer_graph = _layer_graph_from_rendered(ctx)
    comp = ctx.state["composition"]
    if not isinstance(comp, CompositionArtifacts):
        comp = CompositionArtifacts.model_validate(comp)

    metadata: dict[str, Any] = {
        "version": "v1.0",
        "source": "apply-edits",
        "source_html": str(source_html),
        "parent_run_id": parent_run_id,
        "skipped_layers": skipped,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    return Trajectory(
        run_id=ctx.run_id,
        created_at=datetime.now(),
        brief=(title or "(restored from edited HTML)")[:500],
        design_spec=ctx.state["design_spec"],
        layer_graph=layer_graph,
        agent_trace=[],
        critique_loop=[],
        composition=comp,
        metadata=metadata,
    )


def _layer_graph_from_rendered(ctx: ToolContext) -> list[LayerNode]:
    nodes: list[LayerNode] = []
    for L in sorted(ctx.state["rendered_layers"].values(),
                    key=lambda l: int(l.get("z_index", 0))):
        bbox = L.get("bbox") or {}
        kwargs: dict[str, Any] = dict(
            layer_id=L["layer_id"],
            name=L["name"],
            kind=L["kind"],
            z_index=int(L.get("z_index", 0)),
            bbox=SafeZone(
                x=int(bbox.get("x", 0)),
                y=int(bbox.get("y", 0)),
                w=int(bbox.get("w", 1)),
                h=int(bbox.get("h", 1)),
            ),
            src_path=L.get("src_path"),
        )
        if L["kind"] == "text":
            kwargs.update(
                text=L.get("text"),
                font_family=L.get("font_family"),
                font_size_px=L.get("font_size_px"),
                align=L.get("align"),
                effects=TextEffect(fill=L.get("fill", "#000000")),
            )
        nodes.append(LayerNode(**kwargs))
    return nodes


# --- helpers --------------------------------------------------------------


def _meta_content(doc: BeautifulSoup, name: str) -> str:
    tag = doc.find("meta", attrs={"name": name})
    if tag is None:
        return ""
    return tag.get("content", "") or ""


def _parse_data_uri(uri: str) -> tuple[str, str]:
    """Returns (mime, base64_payload)."""
    header, _, data = uri.partition(",")
    header = header.removeprefix("data:")
    mime = header.split(";", 1)[0]
    return mime, data


def _int_attr(tag: Tag, name: str, default: int) -> int:
    raw = tag.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
