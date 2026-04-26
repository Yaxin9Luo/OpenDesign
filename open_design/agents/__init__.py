"""Pre- and post-planner agents — sub-agents that shape the planner's
input/output without joining the tool-use loop.

Currently:
- `PromptEnhancer` (v2.4): expands raw user briefs into structured
  multi-section enhanced briefs before `planner.start`.
- `ClaimGraphExtractor` (v2.8.0): extracts the paper's argumentative arc
  (thesis / tensions / mechanisms / evidence / implications) when a PDF
  is attached. Output feeds the planner (slide arc) and the critic
  (claim_coverage check).
- `CriticAgent` (v2.7.3): forked vision critic with its own LLMBackend,
  own turn budget, own trajectory file. Spawned per `critique` tool call
  by the planner; replaces the legacy inline `Critic` class.
"""

from .claim_graph_extractor import ClaimGraphExtractor
from .critic_agent import CriticAgent
from .prompt_enhancer import PromptEnhancer, EnhancerResult

__all__ = [
    "PromptEnhancer", "EnhancerResult",
    "ClaimGraphExtractor",
    "CriticAgent",
]
