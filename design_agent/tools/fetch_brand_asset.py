"""fetch_brand_asset — v0 STUB. Always returns not_found.

Reserved for v1 Brand Kit integration (parse a brand PDF into structured assets,
then fetch logos/stamps/illustrations by id).
"""

from __future__ import annotations

from typing import Any

from ._contract import ToolContext, obs_not_found
from ..schema import ToolObservation


def fetch_brand_asset(args: dict[str, Any], *, ctx: ToolContext) -> ToolObservation:
    asset_id = args.get("asset_id", "<unspecified>")
    return obs_not_found(
        f"brand_asset '{asset_id}' not found (v0 stub — Brand Kit not implemented)",
        next_actions=[
            "fall back to a generated/composed alternative",
            "for stamps: render a circular/square text layer with seal styling",
            "for logos: defer to a placeholder rendered via render_text_layer",
        ],
    )
