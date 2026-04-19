"""html_renderer — compose layers into a single self-contained .html file.

Poster mode only (v1.0 #6). Landing mode ships with #8's semantic schema.

Output properties:
- Pixel-accurate absolute-positioned layers matching the layer_graph (1:1
  with the PSD / SVG). Canvas size is preserved verbatim.
- Zero external dependencies: CSS + JS inline, background images as data:
  URIs, fonts embedded as WOFF2 subsets via @font-face.
- Every text layer carries the authoritative state in data-* attrs (source
  of truth for the `apply-edits` CLI round-trip in v1.0 #6.5):
  data-bbox-x / -y / -w / -h, data-font-size-px, data-fill, data-font-family.
  Inline style is derived from these; keep them in sync on every edit.
- In-browser edit toolbar (v1.0 #6):
    * Click any text layer → floating toolbar appears above it with
      font-family dropdown, font-size number input, color picker, and a
      Save button (copy-to-clipboard / download edited HTML).
    * Drag handle (⤢) at the layer's top-left lets users reposition.
    * Double-click into text → native contenteditable for content edits.
  All edits update both inline style and the data-* attrs so that the file
  round-trips losslessly.
"""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

from ._contract import ToolContext
from ._font_embed import build_font_face_css
from ..util.logging import log


def write_html(
    layers: list[dict[str, Any]],
    cw: int,
    ch: int,
    out_path: Path,
    ctx: ToolContext,
) -> None:
    """Write a self-contained poster HTML to out_path.

    `layers` is expected sorted by z_index ascending (matches composite's
    `sorted_layers`); we paint them in that order, letting DOM order drive
    stacking.
    """
    text_layers = [L for L in layers if L["kind"] == "text" and L.get("text")]
    fonts_used: dict[str, set[str]] = {}
    for L in text_layers:
        family = L.get("font_family") or ctx.settings.default_text_font
        fonts_used.setdefault(family, set()).update(L["text"])

    font_face_css = build_font_face_css(fonts_used, ctx)
    bundled_families = sorted(ctx.settings.fonts.keys())

    head = _head_block(cw, ch, font_face_css, _doc_title(ctx),
                       run_id=getattr(ctx, "run_id", "") or "")
    body_parts: list[str] = [
        "<body>",
        _user_comment(),
        f'<div class="canvas" data-w="{cw}" data-h="{ch}">',
    ]
    for L in layers:
        kind = L.get("kind")
        if kind == "background":
            body_parts.append(_background_html(L))
        elif kind == "text" and L.get("text"):
            body_parts.append(_text_html(L, ctx))
        elif kind == "brand_asset":
            body_parts.append(_asset_html(L))
        else:
            body_parts.append(
                f'  <!-- skipped layer kind={kind!r} id={L.get("layer_id", "?")} -->'
            )
    body_parts.append("</div>")
    body_parts.append(_edit_toolbar_html(bundled_families))
    body_parts.append(_save_modal_html())
    body_parts.append(f"<script>{_edit_script(bundled_families)}</script>")
    body_parts.append("</body>")
    body_parts.append("</html>")

    doc = head + "\n".join(body_parts)
    out_path.write_text(doc, encoding="utf-8")
    log("html.written",
        path=str(out_path),
        bytes=out_path.stat().st_size,
        layers=len(layers),
        text_layers=len(text_layers),
        fonts=len(fonts_used))


# --- section builders -----------------------------------------------------


def _head_block(cw: int, ch: int, font_face_css: str, title: str,
                run_id: str = "") -> str:
    run_id_meta = (
        f'<meta name="ld-run-id" content="{_attr(run_id)}">\n' if run_id else ""
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="generator" content="LongcatDesign">\n'
        + run_id_meta
        + f"<title>{html.escape(title)}</title>\n"
        "<style>\n"
        + _base_css(cw, ch)
        + _toolbar_css()
        + _modal_css()
        + f"  {font_face_css}\n"
        "</style>\n"
        "</head>\n"
    )


def _base_css(cw: int, ch: int) -> str:
    return (
        "  html, body { margin: 0; padding: 0; }\n"
        "  body { background: #111; display: flex; justify-content: center;\n"
        "         align-items: flex-start; min-height: 100vh; padding: 24px;\n"
        "         box-sizing: border-box; font-family: system-ui, sans-serif;\n"
        "         color: #eee; }\n"
        f"  .canvas {{ position: relative; width: {cw}px; height: {ch}px;\n"
        "             background: #fff; box-shadow: 0 16px 64px rgba(0,0,0,0.45);\n"
        "             overflow: hidden; }\n"
        "  .layer { position: absolute; top: 0; left: 0; }\n"
        "  .layer.bg { width: 100%; height: 100%; pointer-events: none;\n"
        "              user-select: none; }\n"
        "  .layer.bg img { width: 100%; height: 100%; display: block; }\n"
        "  .layer.brand img { width: 100%; height: 100%; display: block; }\n"
        "  .layer.text { outline: none; line-height: 1.1;\n"
        "                word-break: break-word; overflow: visible;\n"
        "                box-sizing: border-box; cursor: text; }\n"
        "  .layer.text:hover { outline: 1px dashed rgba(120,180,255,0.35);\n"
        "                      outline-offset: 2px; }\n"
        "  .layer.text.ld-active { outline: 1px solid rgba(120,180,255,0.9);\n"
        "                          outline-offset: 2px; }\n"
    )


def _toolbar_css() -> str:
    return (
        "  .ld-drag-handle { position: absolute; top: -14px; left: -14px;\n"
        "                    width: 18px; height: 18px; border-radius: 50%;\n"
        "                    background: rgba(120,180,255,0.9); color: #fff;\n"
        "                    font-size: 11px; line-height: 18px;\n"
        "                    text-align: center; cursor: grab;\n"
        "                    user-select: none; display: none;\n"
        "                    box-shadow: 0 2px 6px rgba(0,0,0,0.4);\n"
        "                    font-family: system-ui; z-index: 10; }\n"
        "  .layer.text.ld-active .ld-drag-handle { display: block; }\n"
        "  .ld-drag-handle.ld-grabbing { cursor: grabbing; background: #4a9eff; }\n"
        "  .ld-toolbar { position: fixed; display: none; z-index: 100;\n"
        "                background: #1f2024; color: #eee;\n"
        "                border: 1px solid #3a3d44;\n"
        "                border-radius: 8px; padding: 6px;\n"
        "                box-shadow: 0 8px 24px rgba(0,0,0,0.5);\n"
        "                font-family: system-ui, sans-serif; font-size: 12px;\n"
        "                gap: 6px; align-items: center; white-space: nowrap; }\n"
        "  .ld-toolbar.ld-visible { display: inline-flex; }\n"
        "  .ld-toolbar select, .ld-toolbar input[type=number] {\n"
        "                background: #2a2d33; color: #eee;\n"
        "                border: 1px solid #3a3d44; border-radius: 4px;\n"
        "                padding: 4px 6px; font-size: 12px; }\n"
        "  .ld-toolbar input[type=number] { width: 56px; }\n"
        "  .ld-toolbar input[type=color] { width: 28px; height: 24px;\n"
        "                padding: 0; border: 1px solid #3a3d44;\n"
        "                border-radius: 4px; background: transparent;\n"
        "                cursor: pointer; }\n"
        "  .ld-toolbar button { background: #2a2d33; color: #eee;\n"
        "                border: 1px solid #3a3d44; border-radius: 4px;\n"
        "                padding: 4px 10px; cursor: pointer; font-size: 12px; }\n"
        "  .ld-toolbar button:hover { background: #363a42; }\n"
        "  .ld-toolbar .ld-label { color: #8a8d94; font-size: 11px;\n"
        "                padding: 0 2px 0 4px; }\n"
        "  .ld-toolbar .ld-save { background: #2a5aa0;\n"
        "                border-color: #3a6ab0; }\n"
        "  .ld-toolbar .ld-save:hover { background: #3269b8; }\n"
    )


def _modal_css() -> str:
    return (
        "  .ld-modal-backdrop { position: fixed; top: 0; left: 0; right: 0;\n"
        "                bottom: 0; background: rgba(0,0,0,0.6); z-index: 500;\n"
        "                display: none; align-items: center;\n"
        "                justify-content: center;\n"
        "                font-family: system-ui, sans-serif; }\n"
        "  .ld-modal-backdrop.ld-visible { display: flex; }\n"
        "  .ld-modal { background: #1f2024; color: #eee; padding: 24px 28px;\n"
        "                border-radius: 10px; max-width: 540px;\n"
        "                box-shadow: 0 20px 60px rgba(0,0,0,0.7);\n"
        "                border: 1px solid #3a3d44; }\n"
        "  .ld-modal h3 { margin: 0 0 8px; font-size: 15px; font-weight: 600; }\n"
        "  .ld-modal p { margin: 0 0 16px; color: #b8bcc4; font-size: 13px;\n"
        "                line-height: 1.5; }\n"
        "  .ld-modal .ld-row { display: flex; gap: 8px;\n"
        "                flex-wrap: wrap; margin-bottom: 10px; }\n"
        "  .ld-modal button { background: #2a5aa0; color: #fff;\n"
        "                border: 1px solid #3a6ab0; border-radius: 6px;\n"
        "                padding: 8px 16px; cursor: pointer; font-size: 13px;\n"
        "                font-family: inherit; }\n"
        "  .ld-modal button.ld-secondary { background: #2a2d33;\n"
        "                border-color: #3a3d44; }\n"
        "  .ld-modal button:hover { filter: brightness(1.15); }\n"
        "  .ld-modal code { background: #0e0f12; padding: 2px 8px;\n"
        "                border-radius: 4px; font-size: 12px; color: #b8d4ff;\n"
        "                font-family: ui-monospace, monospace; }\n"
    )


def _user_comment() -> str:
    return (
        "<!--\n"
        "  LongcatDesign HTML output.\n"
        "  \n"
        "  Click any text layer to activate its edit toolbar:\n"
        "    • double-click text to edit content (contenteditable)\n"
        "    • drag the ⤢ handle (top-left) to reposition the layer\n"
        "    • use the floating toolbar to change font, size, color\n"
        "  Click 💾 Save to copy the edited HTML or download it.\n"
        "  \n"
        "  Edits live in this browser page only. To propagate them to the\n"
        "  PSD / SVG / PNG outputs, run `longcat-design apply-edits <file>`\n"
        "  on the downloaded HTML (v1.0 #6.5).\n"
        "  \n"
        "  Layer state: authoritative source is the data-* attrs on each\n"
        "  .layer element — data-bbox-x/y/w/h, data-font-size-px, data-fill,\n"
        "  data-font-family, data-layer-id, data-kind, data-z-index,\n"
        "  data-layer-name. Inline style is derived and kept in sync.\n"
        "-->"
    )


def _background_html(L: dict[str, Any]) -> str:
    src = _inline_image(L["src_path"])
    return (
        f'  <div class="layer bg" '
        f'data-layer-id="{_attr(L.get("layer_id", ""))}" '
        f'data-kind="background" '
        f'data-z-index="{int(L.get("z_index", 0))}">'
        f'<img src="{src}" alt=""></div>'
    )


def _asset_html(L: dict[str, Any]) -> str:
    src = _inline_image(L["src_path"])
    bbox = L.get("bbox") or {}
    return (
        f'  <div class="layer brand" '
        f'data-layer-id="{_attr(L.get("layer_id", ""))}" '
        f'data-kind="brand_asset" '
        f'data-z-index="{int(L.get("z_index", 0))}" '
        f'data-layer-name="{_attr(L.get("name", ""))}" '
        f'style="left:{int(bbox.get("x", 0))}px; '
        f'top:{int(bbox.get("y", 0))}px; '
        f'width:{int(bbox.get("w", 0))}px; '
        f'height:{int(bbox.get("h", 0))}px;">'
        f'<img src="{src}" alt=""></div>'
    )


def _text_html(L: dict[str, Any], ctx: ToolContext) -> str:
    bbox = L["bbox"]
    bx, by = int(bbox["x"]), int(bbox["y"])
    bw, bh = int(bbox["w"]), int(bbox["h"])
    font_size = int(L["font_size_px"])
    family = L.get("font_family") or ctx.settings.default_text_font
    align = L.get("align") or "left"
    fill = L.get("fill") or "#000000"
    effects = L.get("effects") or {}
    shadow = effects.get("shadow") or {}
    stroke = effects.get("stroke") or {}

    justify = {"left": "flex-start", "center": "center", "right": "flex-end"}[align]
    style_pairs: list[str] = [
        f"left:{bx}px", f"top:{by}px",
        f"width:{bw}px", f"height:{bh}px",
        f"font-family:'{family}'",
        f"font-size:{font_size}px",
        f"color:{fill}",
        f"text-align:{align}",
        "display:flex",
        "align-items:center",
        f"justify-content:{justify}",
    ]
    if shadow:
        dx = int(shadow.get("dx", 0))
        dy = int(shadow.get("dy", 4))
        blur = int(shadow.get("blur", 12))
        color = shadow.get("color", "rgba(0,0,0,0.5)")
        style_pairs.append(f"text-shadow:{dx}px {dy}px {blur}px {color}")
    if stroke and int(stroke.get("width", 0)) > 0:
        sw = int(stroke["width"])
        sc = stroke.get("color", "#000000")
        style_pairs.append(f"-webkit-text-stroke:{sw}px {sc}")

    style = "; ".join(style_pairs)
    inner = html.escape(L["text"])

    # data-* attrs are authoritative for apply-edits round-trip.
    return (
        f'  <div class="layer text" '
        f'data-layer-id="{_attr(L.get("layer_id", ""))}" '
        f'data-kind="text" '
        f'data-z-index="{int(L.get("z_index", 0))}" '
        f'data-layer-name="{_attr(L.get("name", ""))}" '
        f'data-bbox-x="{bx}" data-bbox-y="{by}" '
        f'data-bbox-w="{bw}" data-bbox-h="{bh}" '
        f'data-font-size-px="{font_size}" '
        f'data-fill="{_attr(fill)}" '
        f'data-font-family="{_attr(family)}" '
        f'data-align="{_attr(align)}" '
        f'contenteditable="true" spellcheck="false" '
        f'style="{style}">'
        f'<span class="ld-drag-handle" contenteditable="false" '
        f'title="drag to reposition">⤢</span>'
        f'{inner}</div>'
    )


def _edit_toolbar_html(families: list[str]) -> str:
    opts = "".join(
        f'<option value="{_attr(f)}">{html.escape(f)}</option>' for f in families
    )
    return (
        '<div class="ld-toolbar" id="ld-toolbar">\n'
        '  <span class="ld-label">font</span>\n'
        f'  <select id="ld-family">{opts}</select>\n'
        '  <span class="ld-label">px</span>\n'
        '  <input type="number" id="ld-size" min="8" max="999" step="1">\n'
        '  <span class="ld-label">color</span>\n'
        '  <input type="color" id="ld-color">\n'
        '  <button class="ld-save" id="ld-save">💾 Save</button>\n'
        "</div>"
    )


def _save_modal_html() -> str:
    return (
        '<div class="ld-modal-backdrop" id="ld-modal-backdrop">\n'
        '  <div class="ld-modal" role="dialog" aria-label="Save edited HTML">\n'
        "    <h3>✓ Your edits are live in this page</h3>\n"
        "    <p>Choose how to save:</p>\n"
        '    <div class="ld-row">\n'
        '      <button id="ld-copy">📋 Copy edited HTML</button>\n'
        '      <button id="ld-download">⬇️ Download edited HTML</button>\n'
        '      <button class="ld-secondary" id="ld-close">Cancel</button>\n'
        "    </div>\n"
        "    <p>To regenerate PSD/SVG/PNG from these edits, run on the downloaded file:<br>\n"
        "      <code>longcat-design apply-edits &lt;downloaded-file&gt;</code></p>\n"
        "  </div>\n"
        "</div>"
    )


def _edit_script(families: list[str]) -> str:
    """Return the inline JS as a plain string. No external deps."""
    families_json = json.dumps(families)
    # Use a raw template. Keep it readable; no f-strings so curly braces don't clash.
    template = r"""
(() => {
  const FAMILIES = __FAMILIES__;
  const toolbar = document.getElementById('ld-toolbar');
  const familySel = document.getElementById('ld-family');
  const sizeInp = document.getElementById('ld-size');
  const colorInp = document.getElementById('ld-color');
  const saveBtn = document.getElementById('ld-save');
  const modal = document.getElementById('ld-modal-backdrop');
  const copyBtn = document.getElementById('ld-copy');
  const dlBtn = document.getElementById('ld-download');
  const closeBtn = document.getElementById('ld-close');
  let active = null;
  let dragging = null;

  // --- activation ---
  document.querySelectorAll('.layer.text').forEach(el => {
    el.addEventListener('mousedown', e => {
      // Don't steal focus from native text editing
      if (e.target.classList && e.target.classList.contains('ld-drag-handle')) return;
      setActive(el);
    });
  });

  document.addEventListener('mousedown', e => {
    const layer = e.target.closest && e.target.closest('.layer.text');
    const insideToolbar = e.target.closest && e.target.closest('.ld-toolbar, .ld-modal-backdrop');
    if (!layer && !insideToolbar) setActive(null);
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      if (modal.classList.contains('ld-visible')) closeModal();
      else setActive(null);
    }
  });

  function setActive(el) {
    if (active === el) return;
    if (active) active.classList.remove('ld-active');
    active = el;
    if (!el) { toolbar.classList.remove('ld-visible'); return; }
    el.classList.add('ld-active');
    // Populate toolbar from data-* attrs
    const fam = el.getAttribute('data-font-family') || FAMILIES[0];
    if (!Array.from(familySel.options).some(o => o.value === fam)) {
      const opt = document.createElement('option');
      opt.value = fam; opt.textContent = fam + ' (not bundled)'; familySel.appendChild(opt);
    }
    familySel.value = fam;
    sizeInp.value = el.getAttribute('data-font-size-px') || '';
    colorInp.value = normalizeColor(el.getAttribute('data-fill') || '#000000');
    positionToolbar(el);
    toolbar.classList.add('ld-visible');
  }

  function positionToolbar(el) {
    const rect = el.getBoundingClientRect();
    const tbRect = toolbar.getBoundingClientRect();
    // Prefer above, fall back to below if no room
    let top = rect.top - tbRect.height - 8;
    if (top < 12) top = rect.bottom + 8;
    let left = rect.left;
    const maxLeft = window.innerWidth - tbRect.width - 12;
    if (left > maxLeft) left = maxLeft;
    if (left < 12) left = 12;
    toolbar.style.top = top + 'px';
    toolbar.style.left = left + 'px';
  }

  window.addEventListener('resize', () => { if (active) positionToolbar(active); });
  window.addEventListener('scroll', () => { if (active) positionToolbar(active); }, true);

  // --- inputs ---
  familySel.addEventListener('change', () => {
    if (!active) return;
    const f = familySel.value;
    active.setAttribute('data-font-family', f);
    active.style.fontFamily = "'" + f + "'";
  });
  sizeInp.addEventListener('input', () => {
    if (!active) return;
    const n = parseInt(sizeInp.value, 10);
    if (!(n > 0)) return;
    active.setAttribute('data-font-size-px', String(n));
    active.style.fontSize = n + 'px';
  });
  colorInp.addEventListener('input', () => {
    if (!active) return;
    const c = colorInp.value;
    active.setAttribute('data-fill', c);
    active.style.color = c;
  });

  // --- drag ---
  document.addEventListener('pointerdown', e => {
    if (!e.target.classList || !e.target.classList.contains('ld-drag-handle')) return;
    const layer = e.target.closest('.layer.text');
    if (!layer) return;
    e.preventDefault();
    e.stopPropagation();
    e.target.classList.add('ld-grabbing');
    const canvas = document.querySelector('.canvas');
    const startX = e.clientX, startY = e.clientY;
    const x0 = parseInt(layer.getAttribute('data-bbox-x') || '0', 10);
    const y0 = parseInt(layer.getAttribute('data-bbox-y') || '0', 10);
    dragging = { layer, startX, startY, x0, y0, canvas, handle: e.target };
    layer.setPointerCapture && layer.setPointerCapture(e.pointerId);
  });
  document.addEventListener('pointermove', e => {
    if (!dragging) return;
    const cRect = dragging.canvas.getBoundingClientRect();
    // Canvas may be scaled by browser zoom, but since our CSS uses fixed px,
    // scale factor is (cRect.width / canvas.clientWidth)
    const scale = cRect.width / dragging.canvas.offsetWidth || 1;
    const dx = (e.clientX - dragging.startX) / scale;
    const dy = (e.clientY - dragging.startY) / scale;
    const nx = Math.round(dragging.x0 + dx);
    const ny = Math.round(dragging.y0 + dy);
    dragging.layer.setAttribute('data-bbox-x', String(nx));
    dragging.layer.setAttribute('data-bbox-y', String(ny));
    dragging.layer.style.left = nx + 'px';
    dragging.layer.style.top = ny + 'px';
    if (active === dragging.layer) positionToolbar(dragging.layer);
  });
  document.addEventListener('pointerup', () => {
    if (!dragging) return;
    dragging.handle.classList.remove('ld-grabbing');
    dragging = null;
  });

  // --- contenteditable text tracking (keep data-* in sync is optional —
  // text content is read directly from innerText in apply-edits; no data-* needed) ---

  // --- save modal ---
  saveBtn.addEventListener('click', () => {
    modal.classList.add('ld-visible');
  });
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
  function closeModal() { modal.classList.remove('ld-visible'); }

  function buildEditedHTML() {
    // Strip drag-handle spans so the output is clean and the HTML doesn't
    // accumulate nested copies if the file is round-tripped.
    const clone = document.documentElement.cloneNode(true);
    clone.querySelectorAll('.ld-drag-handle, .ld-toolbar, .ld-modal-backdrop, script').forEach(n => n.remove());
    clone.querySelectorAll('.layer.text.ld-active').forEach(el => el.classList.remove('ld-active'));
    return '<!DOCTYPE html>\n' + clone.outerHTML;
  }

  copyBtn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(buildEditedHTML());
      copyBtn.textContent = '✓ Copied!';
      setTimeout(() => { copyBtn.textContent = '📋 Copy edited HTML'; }, 1500);
    } catch (err) {
      alert('Copy failed: ' + err.message + '. Try Download instead.');
    }
  });

  dlBtn.addEventListener('click', () => {
    const blob = new Blob([buildEditedHTML()], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const stem = (document.title || 'poster').replace(/[^\w\u4e00-\u9fa5-]+/g, '_').slice(0, 40) || 'poster';
    a.href = url;
    a.download = stem + '.edited.html';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    dlBtn.textContent = '✓ Downloaded';
    setTimeout(() => { dlBtn.textContent = '⬇️ Download edited HTML'; }, 1500);
  });

  function normalizeColor(c) {
    // <input type=color> only accepts #rrggbb. Expand #rgb to #rrggbb;
    // reject rgba/hsl by falling back to black.
    if (!c) return '#000000';
    if (/^#[0-9a-fA-F]{6}$/.test(c)) return c.toLowerCase();
    if (/^#[0-9a-fA-F]{3}$/.test(c)) {
      return '#' + c.slice(1).split('').map(ch => ch + ch).join('').toLowerCase();
    }
    return '#000000';
  }
})();
"""
    return template.replace("__FAMILIES__", families_json)


# --- helpers --------------------------------------------------------------


def _inline_image(src_path: str) -> str:
    p = Path(src_path)
    with open(p, "rb") as f:
        data = f.read()
    ext = p.suffix.lower().lstrip(".")
    mime = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp", "gif": "image/gif",
    }.get(ext, "image/png")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _attr(s: str) -> str:
    return html.escape(s, quote=True)


def _doc_title(ctx: ToolContext) -> str:
    spec = ctx.state.get("design_spec")
    if spec is not None:
        brief = getattr(spec, "brief", None)
        if brief:
            return brief[:80]
    return "LongcatDesign output"
