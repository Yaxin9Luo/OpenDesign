"""fetch_brand_asset — v0 STUB. Always returns not_found.

Reserved for v1 Brand Kit integration (parse a brand PDF into structured
assets, then fetch logos/stamps/illustrations by id).
"""

from __future__ import annotations

from typing import Any

from ._contract import ToolContext, obs_error
from ..schema import ToolResultRecord


def fetch_brand_asset(args: dict[str, Any], *, ctx: ToolContext) -> ToolResultRecord:
    asset_id = args.get("asset_id", "<unspecified>")
    return obs_error(
        f"brand_asset '{asset_id}' not found (v0 stub — Brand Kit not implemented)",
        category="not_found",
        payload={"asset_id": asset_id},
    )
