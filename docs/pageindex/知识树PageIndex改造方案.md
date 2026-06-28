# 知识树 PageIndex 改造方案

## 问题现状

当前知识树（`知识树.json`）的节点结构：

```json
{
  "level": 3,
  "number": "3.2.1",
  "title": "桩基础",
  "content": "桩基施工前应进行试桩，确定施工工艺参数。桩基成孔后必须进行清孔处理……",
  "children": [……]
}
```

每个节点存放的是原文全量，没有摘要。GenChecklistState 的 LLM 在筛选相关条款时，需要先把全部原文读完，才能判断哪些条款与当前方案类型相关。标准的文档有几千条条款，每次调用 token 消耗巨大，速度慢。

## 参考方案

参照 [PageIndex](https://github.com/VectifyAI/PageIndex)，其核心思路是：

1. 将文档解析成层次树——每层的节点都有对应的标题、主要内容、子节点
2. 使用 LLM 为每个节点生成摘要，叶子节点摘内容是啥，父节点摘子节点内容
3. 树结构仅作为导航索引，不含全文——原文通过页号取回
4. LLM 导航时只看摘要就能决定是否往下深入

## 改造方案

增加一个 GenSummaryState（生成摘要）状态机，放在文档解析和构建知识树之间。

### 改造后流水线

```
WordParseState（已有）
  产出 _chunks 树 + _full_text
           ↓
GenSummaryState（新建）
  自底向上遍历 _chunks 树，对每个节点调 LLM 生成摘要
  叶子节点 → summary（该条款的摘要）
  父节点   → prefix_summary（自身内容摘要）+ summary（聚合子节点的综合摘要）
  产出 _chunks（多了 summary、prefix_summary）
           ↓
BuildKnowledgeState（改）
  DFS 分配 node_id，在 _full_text 中定位 start_index/end_index
  落盘 知识树.json（有摘要，无全文）
```

### 节点结构

内存中 HeadingChunk 新增字段：

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| level | int | 已有 | 层级深度 |
| number | str | 已有 | 文档自身编号，如 "3.2.1" |
| title | str | 已有 | 章节标题 |
| content | str | 已有 | 标题下的直接正文，内存用，不落盘 |
| children | list | 已有 | 子节点 |
| node_id | str | 新增，BuildKnowledgeState 分配 | 系统统一编号 "0001"-"9999" |
| start_index | int | 新增，BuildKnowledgeState 计算 | 原文在 _full_text 中的起始偏移 |
| end_index | int | 新增，BuildKnowledgeState 计算 | 原文在 _full_text 中的结束偏移 |
| summary | str | 新增，GenSummaryState 生成 | 综合摘要（含子节点内容） |
| prefix_summary | str | 新增，GenSummaryState 生成 | 自身内容摘要（不含子节点） |

### 落盘结构（知识树.json）

```json
{
  "node_id": "0012",
  "number": "3.2.1",
  "title": "桩基础",
  "start_index": 8450,
  "end_index": 9120,
  "summary": "桩基施工前应进行试桩确定工艺参数，成孔后必须清孔处理",
  "prefix_summary": "",
  "children": [……]
}
```

### BuildKnowledgeState 三阶段

```
parse     → 轮询 WordParseState
summarize → 轮询 GenSummaryState
build     → 线程：分配 node_id + 定位 index + 落盘
```

### 关于 number 与 node_id

两者并存、各有用途：

- `number` 是文档自带的章节编号（语义化，给人看），格式不统一——有的规范用"第X条"，有的用"3.2.1"
- `node_id` 是系统统一分配的递增序号（格式化，给机器查），如 "0001"、"0002"

### 待建文件

- `state/state_review.py` — GenSummaryState 状态机
- `workspace/prompts/files/生成节点摘要.md` — LLM 摘要提示词模板
