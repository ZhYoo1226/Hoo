# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

OpenSolver — 基于有限状态机(FSM)的 AI 助手框架。通过"呼吸循环"驱动状态生命周期，LLM 输出 JSON action 指令来创建/销毁状态、调用工具，实现自主对话和任务执行。

## 常用命令

```bash
# 启动（Windows PowerShell）
.venv312\Scripts\activate; python app.py

# 运行测试（全部）
.venv312\Scripts\activate; python -m pytest test_app.py -v -s

# 运行测试（指定函数）
.venv312\Scripts\activate; python -m pytest test_app.py -v -s -k "test_func_name"
```

依赖管理固定使用 `.venv312`（Python 3.12），安装新依赖用 `uv` 指定解释器：`uv pip install <pkg> --python .venv312/Scripts/python.exe`。

## 核心架构

### 呼吸循环（主循环）

`app.py` 创建 `SolverOwner` (继承自 `StateOwner`)，添加 `LiveState` 后进入 `run_server()` → `g_owner.breathe(1)` 循环。每次呼吸：检查超时 → 删除过期状态 → 执行所有活跃状态的 `Execute()`。

### 状态机系统

- **BaseState** (`state/base_state.py`): 状态基类，生命周期为 `Enter(owner)` → `Execute(owner)` → `Exit(owner)`。每个状态有 `_duration`（超时时间）和 `_uuid`。
- **StateOwner** (`state/base_state.py`): 状态管理器，维护活跃状态列表、聊天记录(deque)、工具列表、记忆系统。核心方法：`add_state()`, `remove_state()`, `breathe()`, `execute_prompt()`, `execute_action()`。
- **状态自动发现**: `_list_state_tools()` 扫描 `state/` 目录下所有模块，找到继承 `BaseState` 且声明了 `actionName` 的类，自动注册为可用工具。只有声明 `actionName` 的状态才能被 LLM 调用。

### Action 执行链路

1. LLM 输出包含 ` ```action {json} ``` ` 代码块
2. `extract_action_json()` 解析出 action JSON（`{"action": "state:XXX", ...}` 或 `{"action": "tool_name", ...}`）
3. `execute_action()` 判断：`state:` 前缀 → 查找状态类、实例化、`add_state()`；否则 → `g_tool_registry.execute()` 调用注册的工具函数

### 提示词系统

- 所有提示词存在 `workspace/prompts/` 目录下，启动时递归扫描 `.md` 文件构建 `PromptIndex`
- 提示词中 `{{variable}}` 占位符在执行时替换，值优先从 state 属性查找，其次 owner 属性
- `owner.execute_prompt()` 加载提示词 → 组装对话 → 请求 LLM → 解析返回的 action JSON

### 工具注册

`action/functions.py` 中用 `@g_tool_registry.register("name")` 装饰器注册工具函数。函数签名通过 `inspect` + AST 解析生成 tool schema（参数名、类型、注释作为描述），暴露给 LLM 作为可用工具列表。

### 关键目录

| 目录 | 用途 |
|------|------|
| `workspace/prompts/` | 提示词模板（系统提示词、任务提示词等） |
| `workspace/mem/` | 记忆数据（Engram 向量记忆 + 个人信息） |
| `workspace/domain_models/` | 领域行为模型（md 文件，启动时加载到记忆） |
| `workspace/chat/` | 每日聊天记录 `chat_YYYYMMDD.md` |
| `workspace/task/` | 任务目录，每个任务一个子目录含 `任务执行.md` |
| `workspace/log/` | 系统日志（`运行状态.log`, `sys_breathe_*.log` 等） |
| `l2p/` | PDDL 规划子模块（LLM 驱动的任务规划） |

### 记忆系统

`owner.memory(name)` 获取/创建 Engram 向量记忆，支持 `observe()` 存储和 `recall()` 检索（hybrid/cosine/spreading 三种模式）。领域模型在 `ScanDomainModelState` 中批量加载到记忆。

## 关键约定

- 状态机之间零耦合，只关注输入-处理-输出，编排交给外部（SolverOwner）
- 允许失败不允许崩溃，用日志记录认知盲区而非 raise 异常
- `# FIXME` 注释是审核检查点，不可随意删除
- 测试函数写入 `zy-test_app.py`，不向 `test_app.py` 添加新测试
- 内部函数名动词开头，保持完整逻辑颗粒度
- 高频调整项提取到文件顶层常量
