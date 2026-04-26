# Implementation Plan — v2.7.2 / v2.7.3 / v2.8.0 / v2.8.1

**作用域**：本文档把 [export-block-eager-wilkinson.md](./export-block-eager-wilkinson.md) 里识别出来的 4 条演进路径（Path G / Path C-vision-fork / Path E / Path F）落成 4 个独立可发布的 release 计划。每个 release 自带 schema 迁移、文件清单、smoke 验证、dogfood gate。

**用户已确认的架构决定**（2026-04-26）：
1. Vision critic sub-agent 看到 **slide PNG + DesignSpec + paper raw_text + claim graph**
2. Critic 输出 **verdict + structured issue list**（不输出 patch）
3. claim_graph_extractor 是 **独立 sub-agent**，输出严格 JSON schema 给 planner 消费

**用户已确认的优先级**：
- v2.7.2（Path G + section renumber）：顺手做，防御性
- v2.7.3（Vision critic sub-agent）：架构改造，user 认为我们当前 critic 设计不对
- v2.8.0（Path E claim graph）：质变投入，主线
- v2.8.1+（Path F archetype 库）：增量做，视觉一致性

**显式废弃**：Path B（HTML deck）—— Cloud Design 实证显示对方也产出 PptxGenJS 原生可编辑 .pptx，editability 不是护城河。

---

## 跨 release 共识

### 兼容性策略

每个 schema 字段加默认值（`= None` / `= []` / `= "evidence_snapshot"`），旧 trajectory / 旧 spec 仍可加载。`apply-edits` round-trip 必须通过 smoke。

### 不要做的事

- 不引入框架（LangGraph / CrewAI）—— sub-agent 仍走 Anthropic SDK + 手写 tool loop。
- 不嵌字体（保持 Arial/Noto SC + system font fallback）。
- 不重写 `LLMBackend`，sub-agent 各自实例化，但都通过 `make_llm_backend(settings)`。
- 不 hardcode 任何 model id。critic 走 `settings.critic_model`，extractor 走 `settings.claim_graph_model`，新增的都从 env 读。

### Trajectory 切分

每个 sub-agent 独立写 trajectory 文件，方便 SFT/DPO 切分：
- `out/<run>/trajectory/planner.jsonl`（已有，保留）
- `out/<run>/trajectory/enhancer.jsonl`（已有）
- `out/<run>/trajectory/claim_graph_extractor.jsonl`（v2.8.0 新增）
- `out/<run>/trajectory/critic.jsonl`（v2.7.3 新增）

每条 jsonl 行就是一次 model call 的 messages + response。

---

## v2.7.2 — Stable-id notes binding + section renumber

**目标**：消除 Cloud Design 已经踩过的两个 bug（notes off-by-one cascade + 章节号非单调）。**1–2 天工作量**，纯防御性，不依赖任何其他 release。

### Schema 改动 (`open_design/schema.py`)

```python
# SlideNode 新增两个 optional 字段
class SlideNode(BaseModel):
    # ... 现有字段 ...
    speaker_notes: str | None = None  # 替代任何 position-based notes 列表
    section_number: str | None = None  # planner 填，例如 "§2.2"，可为 None
```

```python
# Settings 新增策略开关
class Settings(BaseSettings):
    # ... 现有字段 ...
    section_number_policy: Literal["renumber", "strip", "preserve"] = "renumber"
```

### 新增文件

`open_design/util/section_renumber.py`
- `def renumber_sections(slides: list[SlideNode]) -> list[SlideNode]`
  - 按当前 slides 顺序，分配 `§1`, `§1.1`, `§1.2`, `§2`, ...
  - 子节奏检测：相邻 slide 标题前缀相同 → 同一 section（增加子号）
  - 不修改原 list（immutability），返回新 list
- `def strip_sections(slides: list[SlideNode]) -> list[SlideNode]`
  - 把所有 `slide.section_number` 设为 None（也不修改原 list）
- `def apply_section_policy(slides, policy) -> list[SlideNode]`
  - 路由到上面两个函数；`policy="preserve"` 直接返回 deepcopy

### 改动文件

| 文件 | 改动 |
|---|---|
| `open_design/tools/pptx_renderer.py` | 写 notes 改为从 `slide.speaker_notes` 读（不要从外部 list 按 enumerate index 取）；写 title 时如果 `slide.section_number` 非空，prepend 到 title |
| `open_design/tools/composite.py` | 在 `_composite_deck` 里调用 `apply_section_policy(slides, settings.section_number_policy)` 后再交给 renderer |
| `open_design/runner.py` | 同步处理 landing/poster 的 section_number（如果适用，否则跳过）|
| `prompts/planner.md` | 加一段："`section_number` 是可选字段；默认会被 v2.7.2 renumber，不要为了'保持稳定'去维护它" |

### Smoke 新增（`open_design/smoke.py`）

```
smoke #25: section_number_policy=renumber 把 [§3.1, §2.2, §3.2] 重排为 [§1, §2, §3]
smoke #26: section_number_policy=strip 把所有 section_number 清空
smoke #27: 构造 4-slide deck，故意 reorder（[s4, s1, s3, s2]），写完读回，每张 slide 的 speaker_notes 正确跟随 slide_id（不是 position）
```

### Dogfood gate

- `uv run python -m open_design.cli run --from-file longcat-next.pdf "design a deck for academic talk"`
- 验证：slide 章节号单调递增；任意 reorder 后 notes 仍跟着原 slide。
- **不需要新模型 / 新 provider** —— 这一版完全是代码改动 + schema 微调。

### 风险

- 极低。schema 字段全 optional + 默认值，向后兼容。
- 唯一风险：`renumber_sections` 的子节奏检测启发式如果错了，会改变现有 dogfood reward。Mitigation：默认 `policy=renumber` 但 ENV `SECTION_NUMBER_POLICY=preserve` 可一键回退。

---

## v2.7.3 — Vision critic as forked sub-agent

**目标**：把 inline critic（`critique_tool.py:26-77`，最多 2 次共享 turn 预算）改造成**带视觉的独立 sub-agent**，自带 turn 预算 + 独立 trajectory。**~1 周工作量**，**与 v2.7.2 并行**（同 wave 1，无 schema 字段依赖）。

### 架构

**当前**：
```
planner agent loop:
  ... → render → composite → critique_tool (inline call) → next_action ...
                                ↑
                          shares planner's turn budget
                          sees: text-only DesignSpec (deck/landing) or preview.png (poster)
                          max 2 calls
```

**v2.7.3 之后**：
```
planner agent loop:
  ... → render → composite → spawn_critic (returns CritiqueReport) → next_action ...
                                  ↓
                           CriticAgent (independent loop)
                                  ↓
                    own LLMBackend + own turn budget (default 10)
                    sees: slide PNGs + DesignSpec + paper raw_text + (claim_graph if v2.8.0+)
                    own trajectory file
                    output: CritiqueReport
```

### Schema 改动 (`open_design/schema.py`)

```python
class CritiqueIssue(BaseModel):
    slide_id: str | None  # None = deck-level issue
    severity: Literal["blocker", "high", "medium", "low"]
    category: Literal[
        "provenance",          # 数字 / 引用没绑 paper
        "claim_coverage",      # 关键论点漏讲（v2.8 后可触发）
        "visual_hierarchy",    # 字号 / 对齐 / 留白
        "typography",          # 字体 / 行距 / 标点
        "layout",              # shape 重叠 / 越界
        "narrative_flow",      # slide 顺序 / 转场
        "factual_error",       # 与 paper 不符
    ]
    description: str  # ≤200 字，具体问题 + 期望
    evidence_paper_anchor: str | None  # e.g. "fig 7" / "table 3" / None

class CritiqueReport(BaseModel):
    score: float  # [0, 1]
    verdict: Literal["pass", "revise", "fail"]
    issues: list[CritiqueIssue]
    summary: str  # 2–3 句给 planner 看的总结
    iteration: int  # 第几轮 critique
```

### 新增文件

`open_design/agents/critic_agent.py`
```python
class CriticAgent:
    def __init__(self, settings: Settings, artifact_type: ArtifactType): ...
    
    def critique(
        self,
        spec: DesignSpec,
        layer_manifest: LayerManifest,
        slide_renders: list[Path],   # PNG 路径，按 slide_id 排序
        paper_raw_text: str | None,
        claim_graph: ClaimGraph | None = None,  # v2.8.0+ 才传
        iteration: int = 1,
    ) -> CritiqueReport: ...
```

内部走和 planner 同样的 Anthropic SDK + 手写 tool loop 模式，但：
- 自己的 `LLMBackend` 实例（model = `settings.critic_model`，默认 `qwen/qwen-vl-max`）
- 自己的 system prompt（每个 artifact_type 一份）
- 自己的 tool registry（见下）
- max_turns = `settings.critic_max_turns`（默认 10）
- 自己的 trajectory 写到 `out/<run>/trajectory/critic.jsonl`

Critic 可用的 tools（最小集合）：
- `read_slide_render(slide_id) -> bytes` —— 读 PNG，base64 inline 给模型
- `read_paper_section(section_id_or_keyword) -> str` —— 从 paper_raw_text 抽相关段落（避免一次塞全文）
- `lookup_claim_node(claim_id) -> ClaimNode` —— v2.8.0+ 启用，查 claim graph 节点
- `report_verdict(report: CritiqueReport)` —— 终态工具，必调；返回后 critic 退出 loop

### 新增 prompt（每个 artifact 类型一份）

| 文件 | 替代 |
|---|---|
| `prompts/critic_vision_deck.md` | 现有 `critic-deck.md`（text-only on layer_graph） |
| `prompts/critic_vision_landing.md` | 现有 `critic-landing.md`（text-only on section tree） |
| `prompts/critic_vision_poster.md` | 现有 `critic.md`（已经是 vision，但要换成 sub-agent 形态） |

每个 prompt 共享结构：
1. 角色：你是带视觉的 critic sub-agent
2. 输入说明：slide PNG list / DesignSpec / paper raw_text / claim_graph
3. 评估维度（按 artifact_type 定制）
4. 严格输出 schema（CritiqueReport JSON via `report_verdict`）
5. 评分规则（pass ≥0.75 + zero blocker；fail < 0.5；其他 revise）
6. **provenance 优先**："任何数字 / 直接引用 / 论文术语，必须能在 paper raw_text 找到 substring；找不到 → severity=blocker, category=provenance"

### 改动文件

| 文件 | 改动 |
|---|---|
| `open_design/tools/critique_tool.py` | 从 inline LLM call 改为 `CriticAgent(settings, artifact_type).critique(...)` 的 thin wrapper；保持 tool 签名不变（planner 看到的是 CritiqueReport JSON）|
| `open_design/runner.py` | `_derive_episode_outcome` 适配 CritiqueReport schema（current verdict 字段沿用）；trajectory 收集逻辑加 critic.jsonl |
| `open_design/config.py` | 加 `critic_max_turns: int = 10`、`critic_thinking_budget: int = 8000`（已有则保持）；`critic_model` 已有 |
| `open_design/llm_backend.py` | 验证 `make_llm_backend` 能多次调用产生独立实例（应该已经能） |
| `prompts/planner.md` | 把"critic 是个 inline tool"的措辞换成"critic 是个 sub-agent"；删 `max_critique_iters` 相关本地约束（critic 自己有 max_turns） |

### Smoke 新增

```
smoke #28: spawn CriticAgent on a fixture deck，mock LLM 返回固定 CritiqueReport，验证 trajectory 写到独立文件
smoke #29: CriticAgent.critique() 在 max_turns 用完时强制返回 fail verdict（不能死循环）
smoke #30: CritiqueReport JSON 能被 planner 的 next_action 正确消费（pass → finalize；revise → propose_design_spec；fail → terminal）
smoke #31: 多模态：critic 收到 base64 PNG 时不会 OOM（长尾测试）
```

### Dogfood gate

- `uv run python -m open_design.cli run --from-file longcat-next.pdf "academic talk deck"`
- 期望：critic 看到的 slide PNG 包含全部 15 张；critic_trajectory.jsonl 单独存在；至少触发 1 次 provenance issue（如果 paper 数字没绑）
- **预算告警**：critic max_turns=10 + 多模态 PNG → 每次 dogfood +30~60% API cost。**用户在 dogfood 前必须确认。**

### 风险

- 中等。两条主要风险：
  1. CriticAgent 自己跑死循环（model 不调 `report_verdict`）→ max_turns 兜底 + 末轮强制 fail。
  2. PNG base64 体积爆炸（一张 1920×1080 PNG ≈ 200KB → 一次 ≈ 3MB messages）→ critic 用 `read_slide_render(slide_id)` tool 按需调用，不一次性塞全部 PNG。
- 老 `critique_tool.py` 的 inline 路径**完全删除**，不保留 fallback。从 v2.7.3 开始 critic 永远是 sub-agent。

---

## v2.8.0 — Claim graph extractor + planner consumption

**目标**：增加 paper → talk 的论证结构改写能力。新增独立 sub-agent `ClaimGraphExtractor`，输出严格 JSON schema 的 ClaimGraph 给 planner 消费。**~2 周工作量**，依赖 v2.7.3 完成（critic 需要能消费 claim_graph 做 coverage 检查）。

### Pipeline 改动

```
当前：     enhancer → planner → ... → critic
v2.8.0：   enhancer → claim_graph_extractor (if PDF input) → planner → ... → critic
```

`claim_graph_extractor` 跳过条件：
- 输入不是 PDF（`--from-file` 不含 .pdf）
- 用户传 `--no-claim-graph`
- 任一条件满足 → ClaimGraph = None，planner 行为退化到 v2.7.3

### Schema 改动 (`open_design/schema.py`)

```python
class TensionNode(BaseModel):
    id: str  # "T1", "T2", ...
    name: str  # 短标签，例如 "understanding-generation conflict"
    description: str  # 1–2 句
    evidence_anchor: str | None  # 论文位置，例如 "fig 7" / "section 3.2"

class MechanismNode(BaseModel):
    id: str  # "M1", ...
    name: str  # "DiNA paradigm"
    resolves: list[str]  # 解决哪些 tension（id 引用）
    description: str

class EvidenceNode(BaseModel):
    id: str  # "E1", ...
    metric: str  # 例如 "ImageNet top-1: 72.3%"
    source: str  # paper 内位置，"table 3"
    raw_quote: str  # **必须**是 paper raw_text 的 substring（v2.7 provenance 规则）
    supports: list[str]  # 支撑哪些 mechanism（id 引用）

class ImplicationNode(BaseModel):
    id: str  # "I1", ...
    description: str
    derives_from: list[str]  # mechanism / evidence id

class ClaimGraph(BaseModel):
    paper_title: str
    paper_anchor: str  # arxiv id 或 doi
    thesis: str  # ≤30 字的核心论点（"Native multimodality requires unifying tokens, not piping modalities through attachments."）
    tensions: list[TensionNode]
    mechanisms: list[MechanismNode]
    evidence: list[EvidenceNode]
    implications: list[ImplicationNode]
```

```python
class SlideNode(BaseModel):
    # ... 现有 + v2.7.2 字段 ...
    covers: list[str] = []  # claim graph 节点 id 列表，例如 ["T1", "M2", "E5"]
```

```python
class Brief(BaseModel):
    # ... 现有字段 ...
    claim_graph: ClaimGraph | None = None
```

### 新增文件

`open_design/agents/claim_graph_extractor.py`
```python
class ClaimGraphExtractor:
    def __init__(self, settings: Settings): ...
    def extract(self, paper_path: Path, paper_raw_text: str) -> ClaimGraph: ...
```

模式：
- 走 Anthropic SDK + 手写 tool loop
- model = `settings.claim_graph_model`（env `CLAIM_GRAPH_MODEL`，默认 `moonshotai/kimi-k2.6` —— 和 enhancer 同一档，agent-coding 模型适合严格 JSON 输出）
- 可用 tools：`ingest_document`（复用现有）+ `lookup_paper_section(section_keyword) -> str` + `report_claim_graph(graph: ClaimGraph)` 终态工具
- max_turns = `settings.claim_graph_max_turns`（默认 15）
- trajectory → `out/<run>/trajectory/claim_graph_extractor.jsonl`

`prompts/claim_graph_extractor.md`
- 角色：你是论文论证结构抽取器
- 输出 schema 严格示例（thesis 1 句 + 3–7 个 tensions + 3–7 个 mechanisms + 5–15 个 evidence + 3–5 个 implications）
- **硬约束**：每个 EvidenceNode.raw_quote **必须**能在 paper raw_text 找到 substring，否则该 evidence 必须删除（不要编造）
- 编号约定（T*/M*/E*/I*）

`open_design/util/claim_graph_validator.py`
- `def validate_claim_graph(graph: ClaimGraph, paper_raw_text: str) -> list[str]`
  - 验证每个 EvidenceNode.raw_quote 是 substring → 否则 fail
  - 验证 mechanisms.resolves 引用合法 tension id
  - 验证 implications.derives_from 引用合法 id
  - 返回错误列表（空 = pass）

### 改动文件

| 文件 | 改动 |
|---|---|
| `open_design/runner.py` | enhancer 之后、planner 之前插入 claim_graph_extractor 调用；结果塞进 `Brief.claim_graph`；如果 validate 失败 → 把 graph 设回 None 并 log warning（不 block planner，让它退化到 v2.7.3 行为） |
| `open_design/cli.py` | 加 `--no-claim-graph` flag |
| `prompts/planner.md` | 加一段：当 `claim_graph` 非空时，slide 顺序应该按 talk arc（cover → tensions → mechanisms → evidence → implications → takeaways），不是 paper 章节顺序。每张 slide 应该填 `covers` 字段标记覆盖的节点 id |
| `prompts/critic_vision_deck.md` | 加 `claim_coverage` 评估维度：未被任何 slide.covers 引用的 tension/mechanism → severity=high；evidence 漏 → severity=medium |
| `open_design/util/provenance.py` | 扩展现有 provenance validator 也校验 ClaimGraph 的 evidence quote substring |
| `open_design/config.py` | `claim_graph_model: str = "moonshotai/kimi-k2.6"`、`claim_graph_max_turns: int = 15` |

### Smoke 新增

```
smoke #32: ClaimGraphExtractor 在 mock LLM 下输出有效 ClaimGraph，validator 通过
smoke #33: validator 拒绝带伪造 raw_quote（不在 paper raw_text）的 EvidenceNode
smoke #34: planner 收到 ClaimGraph 时正确填充 SlideNode.covers
smoke #35: critic 检测到 claim graph 节点漏讲，触发 claim_coverage issue
smoke #36: --no-claim-graph 时 pipeline 退化到 v2.7.3 行为，不报错
```

### Dogfood gate

- `uv run python -m open_design.cli run --from-file longcat-next.pdf "academic talk deck"`
- 期望：
  - `claim_graph_extractor.jsonl` 写出 ClaimGraph，validator 通过
  - 至少识别 LongCat-Next 的关键 thesis（e.g. "lexicalize modalities as discrete tokens"）和核心 tensions（understanding-generation conflict, dual bottleneck）
  - planner 产出的 deck 顺序不再是论文章节顺序
  - critic 不再报 claim_coverage 漏讲（如果还报，迭代 prompt）
- **目标 reward**：v2.7.3 longcat-next dogfood reward × 1.05 以上（5% 提升即视为 Path E 起作用）

### 风险

- 高（这是质变投入）。3 条风险：
  1. **claim graph 抽错** → planner 按错误结构排 slide → 整体退化。Mitigation：validator 严格 + provenance 强制 + 抽取失败时退化到 v2.7.3 行为。
  2. **slide.covers 字段被 planner 忽略** → critic 报 claim_coverage 但 planner 不响应。Mitigation：critic verdict=revise 时 planner 强制 propose_design_spec 一次。
  3. **API cost 进一步上升** → enhancer + extractor + planner + critic 四个 sub-agent 各自跑。Mitigation：claim_graph_extractor 一次跑，结果在 trajectory 缓存，重跑 dogfood 时复用。

---

## v2.8.1 — Slide archetype 库（Phase 1）

**目标**：把"标题+内容+图"单一版式扩展到 8–10 种 archetype。**~1–2 周工作量 per phase**，分 3 phase 增量上线，每 phase 互不阻塞。

### 架构

```python
# open_design/schema.py
SlideArchetype = Literal[
    # Phase 1（v2.8.1）
    "cover_editorial",       # 大标题 + 小副标 + 作者带，serif
    "evidence_snapshot",     # 1 个大数字 + 1 句话脚注
    "takeaway_list",         # 3 bullet + 一行口号
    "thanks_qa",             # 致谢 + 联系方式 + Q&A 提示
    
    # Phase 2（v2.8.2）
    "pipeline_horizontal",   # 横向 stage 链
    "tension_two_column",    # 左：旧痛点；右：新解法
    "section_divider",       # 章节分隔，大数字 + 章节名
    
    # Phase 3（v2.8.3）
    "residual_stack_vertical",       # 纵向 layer 累积
    "conflict_vs_cooperation",       # 前后对比卡片
    "cover_technical",               # 技术风封面，mono + grid
]

class SlideNode(BaseModel):
    # ... 现有 + v2.7.2 + v2.8.0 字段 ...
    archetype: SlideArchetype = "evidence_snapshot"  # 默认值 = 当前行为
```

### 渲染器重构（`open_design/tools/pptx_renderer.py`）

当前 `pptx_renderer.py` 的 slide 渲染逻辑变成 dispatch table：

```python
ARCHETYPE_RENDERERS = {
    "cover_editorial": _render_cover_editorial,
    "evidence_snapshot": _render_evidence_snapshot,
    # ...
}

def render_slide(slide: SlideNode, slide_obj, ...):
    renderer = ARCHETYPE_RENDERERS.get(slide.archetype, _render_evidence_snapshot)
    renderer(slide, slide_obj, ...)
```

每个 `_render_*` 函数：
- 是 self-contained 的 layout function
- 输入：SlideNode + python-pptx Slide object + theme tokens
- 输出：在 Slide 上摆放原生 shape（不返回值）
- 必须是 deterministic（同 input → 同 output）—— 方便 smoke 截图比对

### 新增文件（按 phase）

**Phase 1（v2.8.1）**：
- `open_design/tools/archetypes/__init__.py`
- `open_design/tools/archetypes/cover_editorial.py`
- `open_design/tools/archetypes/evidence_snapshot.py`
- `open_design/tools/archetypes/takeaway_list.py`
- `open_design/tools/archetypes/thanks_qa.py`
- `open_design/tools/archetypes/_common.py`（共享：theme token 解析、字体应用、shape grid helper）

**Phase 2/3** 类似，每加一个 archetype 一个文件。

### 改动文件

| 文件 | 改动 |
|---|---|
| `open_design/tools/pptx_renderer.py` | 主 render 函数改成 dispatch；老 inline 渲染逻辑搬到 `evidence_snapshot.py`（保持向后兼容默认值） |
| `prompts/planner.md` | 加 archetype 选择规则（cover slide → cover_*；results 单数字 → evidence_snapshot；多 stage 流程 → pipeline_horizontal；最后一页 → thanks_qa；倒数第二 → takeaway_list） |
| `prompts/critic_vision_deck.md` | 加 archetype 一致性检查：archetype 应该匹配 slide 内容，否则 issue.category=visual_hierarchy |

### Smoke 新增（每个 archetype 一项）

```
Phase 1:
  smoke #37: cover_editorial 渲染产出 ≥3 个 shape，包含大标题 text frame
  smoke #38: evidence_snapshot 渲染产出 1 个超大数字 + 1 个脚注 text frame
  smoke #39: takeaway_list 渲染产出 3 个 bullet shape
  smoke #40: thanks_qa 渲染产出致谢段 + 联系方式段
  smoke #41: archetype 字段缺失时 fallback 到 evidence_snapshot 不报错
  smoke #42: 同 SlideNode 重复渲染两次，shape 数量和位置完全一致（determinism）
```

### Dogfood gate per phase

- Phase 1 上线后：`uv run python -m open_design.cli run --from-file longcat-next.pdf "academic talk deck"` 应该看到至少 cover + evidence + takeaway + thanks 4 种 archetype 出现，视觉一致性 critic 给 ≥0.85。

### 风险

- 低-中。每 phase 加 archetype 是增量，不破坏现有。
- 主要风险：planner 选错 archetype（把 conflict 内容塞进 evidence_snapshot）→ critic 兜底 + dogfood 验证。

---

## 跨 release 验证矩阵

| Release | 新 smoke | 新 dogfood | 总 smoke 数（累计） | 期望 longcat-next reward |
|---|---|---|---|---|
| v2.7.2 | #25–27 | 同 v2.7（验证 notes 跟 id） | 27 | 0.86–0.90（不应退化） |
| v2.7.3 | #28–31 | longcat-next + critic_trajectory.jsonl 检查 | 31 | 0.87–0.92（vision critic 应小幅提升） |
| v2.8.0 | #32–36 | longcat-next + claim_graph 输出检查 + slide 顺序非论文章节序 | 36 | **目标 ≥0.93**（质变） |
| v2.8.1 | #37–42 | longcat-next + 4 archetype 出现 | 42 | 0.93+（视觉一致性提升，reward 应该 plateau 高位） |

---

## 实施顺序（2-wave staged parallel）

**Wave 1（并行）**：
- worktree A：v2.7.2 (Path G + section renumber)，从 main 分叉
- worktree B：v2.7.3 (Vision critic sub-agent)，从 main 分叉
- 两者 merge 到 `wave1-integration` branch，跑 smoke 验证联合 schema 无冲突，再 merge 到 main，tag `v2.7.2` 和 `v2.7.3`

**Wave 2（并行）**：
- worktree C：v2.8.0 (Claim graph)，从 main（已含 Wave 1）分叉，CriticAgent 已就位可直接接 claim_graph
- worktree D：v2.8.1 Phase 1 (Archetype × 4)，从 main 分叉，SlideNode 字段已就位
- 两者 merge 到 `wave2-integration` branch，跑 smoke 验证 + dogfood，再 merge 到 main，tag `v2.8.0` 和 `v2.8.1-phase1`

**Final 验证**：在 main 上跑一次完整 dogfood（longcat-next paper → academic talk deck），对比 v2.7 baseline reward。

每个 release 独立 commit message，PR 可以两两合并发或分开发，看你 review 节奏。

---

## 文档同步

每个 release commit 时同步：
- `docs/ROADMAP.md` —— 移动 release 从 "next" 到 "shipped"
- `docs/DECISIONS.md` —— 记录新 sub-agent 架构决定（v2.7.3 / v2.8.0）
- `docs/GOTCHAS.md` —— 如果 dogfood 撞到新坑，记录
- `CLAUDE.md` —— 更新 "Current Near-Term Priorities" 和 "Provider routing"（如果 critic_model / claim_graph_model 变）
- `GPT.md` —— 如果 schema 改动影响数据契约，更新（v2.7.2 加 speaker_notes / section_number；v2.8.0 加 ClaimGraph 整套）

---

## 开干前最后一次 sanity check

- [ ] 用户确认所有 4 个 release 的优先级和顺序
- [ ] 用户确认 dogfood 的 API 预算上限（v2.7.3 之后每次 +30~60%；v2.8.0 之后再 +20~40%）
- [ ] 用户确认本计划保存位置（当前 `~/.claude/plans/`，可移到 `docs/IMPLEMENTATION_v2.8.md` 进 git）
- [ ] 用户确认是否需要并行 worktree（每个 release 一个 worktree 还是 main 串行）

确认后从 v2.7.2 开始。
