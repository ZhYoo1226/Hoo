# zy__五一

这份文档分三部分：

1. 讲明白 `WordParseState` 里的标题栈是怎么工作的，输出形式是什么，代码原理是什么。
2. 对比 `state` 目录整体状态机风格与 `state_parse.py` 的差异，分析它现在为什么不像原始的“多线程 + 事件驱动状态机”，以及如果往原框架靠，应该怎么改。
3. 结合老板布置的任务，讲讲应该如何着手研究 `openclaw` 和 `harness` 的提示词，以及老板的真实意图是什么。

---

## 一、标题栈到底是怎么工作的

### 1. 先说结论

当前 `WordParseState` 的切分策略，核心就是：

```text
识别标题 -> 用栈维护“当前标题路径” -> 普通正文挂到当前栈顶标题 -> 最终形成一棵 HeadingChunk 树 -> 再把树拍平成文件输出
```

所以你前面的理解基本是对的：

```text
拿出每个标题，然后把正文附在最近的标题上
```

但更准确地说，不是“最近标题”这么模糊，而是：

```text
正文永远追加到当前栈顶标题的 content 里。
```

而二级标题、三级标题也不是“文字贴在一级标题下面”，而是：

```text
作为一级标题的 children 节点挂上去。
```

所以最终结果是树，不是简单字符串拼接。

### 2. 标题栈的本质

在 `_split_text_chunks()` 里，有三个关键变量：

```python
roots: List[HeadingChunk] = []
stack: List[HeadingChunk] = []
intro_lines: List[str] = []
```

含义分别是：

- `roots`：所有根标题，也就是一级入口节点
- `stack`：当前正在生效的标题路径
- `intro_lines`：在还没遇到任何标题前出现的正文

最关键的是 `stack`。

不要把它理解成“收集所有标题的列表”，而应该理解成：

```text
当前阅读位置所处的标题路径
```

例如，如果文档读到这里：

```text
第一章 总则
1.1 编制依据
1.1.1 国家规范
```

那么 `stack` 大致就是：

```text
[
  第一章 总则,
  1.1 编制依据,
  1.1.1 国家规范
]
```

也就是说：

- 栈底是更高层标题
- 栈顶是当前最具体、最靠近正文的标题

所以：

```python
stack[-1]
```

就表示：

```text
当前正文应该挂到哪个标题下面
```

### 3. 标题先怎么被识别出来

在 `_split_text_chunks()` 里，代码会逐段读取 docx 段落，把每一段变成：

```text
(style_name, text)
```

然后判断这段是标题还是正文。

判断标题主要有三条路：

1. 目录样式 `toc 1 / toc 2 / toc 3`
2. Word 的 `Heading 1 / Heading 2 / Heading 3` 样式
3. 正则识别标题文本，比如 `一、总则`、`1.1 编制依据`

同时排除这种伪标题：

```text
4.7.10.6.压浆使用活塞式压浆泵，不得使用压缩空气。
```

因为这其实更像编号正文条款，不是真标题。

### 4. 栈是如何把标题聚成树的

真正核心的代码是：

```python
heading_level, heading_number, heading_title = heading_info
chunk = HeadingChunk(
    level=heading_level,
    number=heading_number,
    title=heading_title,
    content="",
)
while stack and stack[-1].level >= heading_level:
    stack.pop()
if stack:
    stack[-1].children.append(chunk)
else:
    roots.append(chunk)
stack.append(chunk)
```

这几行就是整套标题树的核心算法。

#### 第一步：先创建一个标题节点

比如当前识别出：

```text
1.1 编制依据
```

那么先构造：

```text
HeadingChunk(level=2, number="1.1", title="编制依据")
```

此时只是“造出一个节点”，还没决定挂到哪里。

#### 第二步：弹栈，找到它应该挂的父标题

这句最关键：

```python
while stack and stack[-1].level >= heading_level:
    stack.pop()
```

含义是：

```text
如果当前栈顶标题的层级 >= 新标题层级，说明新标题不是它的孩子，必须退回上层去找父节点。
```

为什么是 `>=` 而不是 `>`？因为：

- 来了一个同级标题，前一个同级标题已经结束了，也必须弹掉
- 来了一个更高层标题，也要一路弹掉，直到回退到合适位置

#### 第三步：挂到父标题下面，或者成为根标题

弹栈结束后：

- 如果 `stack` 还不空，说明找到了父标题
- 如果 `stack` 为空，说明这是一个新的根标题

所以：

```python
if stack:
    stack[-1].children.append(chunk)
else:
    roots.append(chunk)
```

这一步决定了：

```text
新标题到底挂到谁下面
```

#### 第四步：把自己压栈，成为当前路径的最新标题

```python
stack.append(chunk)
```

从这一刻开始，后面的正文都会挂到这个新标题下面，直到后面遇到更合适的新标题为止。

### 5. 栈变化的具体例子

假设文档顺序是：

```text
1 总则
1.1 编制依据
1.1.1 国家规范
1.2 工程范围
2 施工方法
```

#### 5.1 处理 `1 总则`

开始时：

```text
stack = []
roots = []
```

新标题 level=1。

- 没有可弹的栈
- 栈空，所以放进 `roots`
- 然后压栈

结果：

```text
roots = [1 总则]
stack = [1 总则]
```

#### 5.2 处理 `1.1 编制依据`

新标题 level=2，当前栈顶是 level=1。

判断：

```text
1 >= 2 ? 否
```

所以不弹栈，直接挂到当前栈顶 `1 总则` 的 children 下，再压栈。

结果：

```text
roots = [1 总则]
stack = [1 总则, 1.1 编制依据]
```

树变成：

```text
1 总则
└─ 1.1 编制依据
```

#### 5.3 处理 `1.1.1 国家规范`

新标题 level=3，栈顶 level=2。

判断：

```text
2 >= 3 ? 否
```

不弹栈，挂到 `1.1 编制依据` 下，再压栈。

结果：

```text
stack = [1 总则, 1.1 编制依据, 1.1.1 国家规范]
```

树变成：

```text
1 总则
└─ 1.1 编制依据
   └─ 1.1.1 国家规范
```

#### 5.4 处理 `1.2 工程范围`

新标题 level=2，当前栈顶是 `1.1.1`，level=3。

开始弹栈：

- `3 >= 2`，弹掉 `1.1.1 国家规范`
- 现在栈顶是 `1.1 编制依据`，`2 >= 2`，继续弹掉
- 现在栈顶是 `1 总则`，`1 >= 2` 不成立，停止

然后把 `1.2 工程范围` 挂到 `1 总则` 下，再压栈。

结果：

```text
stack = [1 总则, 1.2 工程范围]
```

树变成：

```text
1 总则
├─ 1.1 编制依据
│  └─ 1.1.1 国家规范
└─ 1.2 工程范围
```

#### 5.5 处理 `2 施工方法`

新标题 level=1。

当前栈：

```text
[1 总则, 1.2 工程范围]
```

弹栈：

- `2 >= 1`，弹掉 `1.2`
- `1 >= 1`，弹掉 `1 总则`
- 栈空

此时说明这是一个新的一级根标题，所以加入 `roots`，再压栈。

结果：

```text
roots = [1 总则, 2 施工方法]
stack = [2 施工方法]
```

### 6. 正文是怎么挂到标题上的

正文的关键逻辑是：

```python
if heading_info is None:
    if stack:
        stack[-1].content = f"{stack[-1].content}\n{text}".strip()
    else:
        intro_lines.append(text)
    continue
```

含义是：

- 如果这一段不是标题
- 且当前栈不空
- 就把这段正文追加到栈顶标题的 `content`

也就是说：

```text
正文永远挂到当前最具体的那个标题，不会自动挂到所有父标题
```

### 7. 最终输出的形式是什么

最终 `_split_text_chunks()` 返回的是：

```python
List[HeadingChunk]
```

也就是一个“根节点列表”。每个 `HeadingChunk` 是：

```python
@dataclass
class HeadingChunk:
    level: int
    number: str
    title: str
    content: str
    children: List["HeadingChunk"] = field(default_factory=list)
```

所以输出不是一段字符串，而是树结构。

例如逻辑上大概长这样：

```text
[
  HeadingChunk(
    title="总则",
    number="1",
    content="",
    children=[
      HeadingChunk(
        title="编制依据",
        number="1.1",
        content="",
        children=[
          HeadingChunk(
            title="国家规范",
            number="1.1.1",
            content="这里是正文A\n这里是正文B",
            children=[]
          )
        ]
      ),
      HeadingChunk(
        title="工程范围",
        number="1.2",
        content="...",
        children=[]
      )
    ]
  ),
  HeadingChunk(
    title="施工方法",
    number="2",
    content="...",
    children=[]
  )
]
```

而真正写成 txt 文件时，是在 `_write_parse_outputs()` 里再把树拍平：

```python
def flatten(nodes):
    flat = []
    for node in nodes:
        flat.append(node)
        flat.extend(flatten(node.children))
    return flat
```

然后逐个写文件。

所以：

```text
栈负责聚树
_write_parse_outputs 负责把树拍平输出文件
```

### 8. 为什么这种写法是合理的

这套写法的本质是：

```text
用“当前标题路径”来建文档树
```

它的好处是：

- 不需要回头重扫全文
- 每读到一个标题，就能立刻确定父子关系
- 正文总能自然归到当前最具体标题
- 最终既能保留树结构，也能拍平成输出文件

一句话总结标题栈：

```text
stack 不是保存“所有标题”，而是保存“当前阅读位置所在的标题路径”；新标题通过弹栈和压栈找到父节点，正文永远挂到当前栈顶标题。
```

---

## 二、state 目录整体风格 vs state_parse.py 的风格差异

### 1. 原始状态机框架的设计风格

从 `state/base_state.py` 看，这个项目原本的状态机框架是：

```text
Enter -> Execute -> Exit
```

再由 `StateOwner.breathe()` 不断循环调用所有状态的 `Execute()`。

`breathe()` 的核心逻辑是：

```text
主线程持续循环
检测超时
处理待删除状态
执行所有状态的 Execute
最后阻塞等待一点时间/事件
```

也就是说，这套框架的原意是：

```text
主线程负责“呼吸”和调度
状态自己在 Enter/Execute/Exit 生命周期里组织工作
```

这是偏事件驱动、偏持续运行的 FSM 风格。

### 2. 原框架里多线程/异步 I/O 的证据

项目里已经有明显例子，说明作者原始设计不是“所有 I/O 都在 Execute 里同步跑完”，而是：

```text
主线程 breathe
子线程做阻塞 I/O 或持续监听
状态负责在线程和主循环之间做桥接
```

典型例子有两个：

#### 2.1 `state/state_keyboard.py`

`WaitInputState` 在 `Enter()` 里启动输入监听线程：

```python
self.listener.start()
```

监听线程内部阻塞读取 stdin：

```python
line = sys.stdin.readline()
```

然后主循环里的 `Execute()` 只做：

```python
got, new_msg = self.listener.get_input(block=False, timeout=0.1)
```

也就是说：

```text
阻塞 I/O 在子线程
主线程只轮询结果
```

#### 2.2 `state/state_solver.py`

这里也有明显例子，在聊天意图处理处有：

```python
self.process_thread = threading.Thread(target=self.process_chat_text_intent, ...)
self.process_thread.start()
```

线程函数里：

```python
while not quit_event.is_set():
    role, chat_new_msg = chat_text_queue.get(timeout=1)
    ...
```

说明作者原本就是把：

```text
消息队列消费 / 阻塞等待 / LLM 推理调度
```

放进工作线程里，然后主线程状态机继续 breathe。

所以从整体风格看，你的判断是对的：

```text
原始开发风格更像“主线程状态调度 + 子线程执行阻塞型 I/O/耗时任务”。
```

### 3. state_parse.py 现在的风格是什么

`state_parse.py` 现在的 `WordParseState.Execute()` 和 `ImgOCRState.Execute()` 都是：

```text
一次 Execute 里同步把整件事跑完，然后立即 remove_state(self)
```

比如：

- `WordParseState.Execute()`：同步转格式、抽表、抽图、抽全文、切 chunk、写文件
- `ImgOCRState.Execute()`：同步遍历图片、逐张 OCR、写回 metadata

最后都直接：

```python
owner.remove_state(self)
```

这意味着它们当前更像：

```text
一次性任务状态
```

而不是原框架那种：

```text
持续呼吸、线程协作、逐步推进的状态
```

### 4. 差异具体在哪里

#### 4.1 生命周期使用不充分

原框架给了：

- `Enter()`
- `Execute()`
- `Exit()`

但 `state_parse.py` 基本只用 `Execute()`，没真正利用：

```text
Enter 做初始化/起线程
Execute 轮询进度
Exit 做清理/收尾
```

#### 4.2 阻塞 I/O 都在主状态 Execute 里同步完成

例如：

- doc/pdf 转换
- docx 解压和 XML 解析
- 图片 OCR
- 大量文件写入

这些都可能阻塞主循环。

如果 `breathe()` 在主线程持续跑，`state_parse.py` 这种同步大任务会让主线程卡很久，不符合原设计“主线程持续呼吸”的节奏。

#### 4.3 没有明显的线程边界和退出信号边界

原状态机风格里，经常有：

- `threading.Thread`
- `quit_event`
- `Enter()` 启线程
- `Exit()` 停线程

而 `state_parse.py` 现在没有这套结构，所以它更像普通同步业务函数，被包装成了状态。

### 5. 如果往原框架靠，应该怎么改

如果严格按原项目风格，`WordParseState` 和 `ImgOCRState` 都更适合改成：

```text
主线程状态调度
子线程做耗时 I/O
Enter 启动线程
Execute 轮询进度
Exit 清理线程/资源
```

#### 5.1 WordParseState 的理想改法

`WordParseState` 可以这样理解：

- `Enter()`：启动一个解析线程
- 线程函数：执行 `_normalize_to_docx -> 抽表 -> 抽图 -> 抽全文 -> 切 chunk -> 落盘`
- `Execute()`：轮询线程是否结束、是否有异常、是否拿到了 `artifact`
- `Exit()`：清理线程句柄、错误状态、必要的 stop event

也就是说，它不应该在一次 `Execute()` 里把所有事情同步做完，而应该把重 I/O 工作放进后台线程。

#### 5.2 ImgOCRState 的理想改法

`ImgOCRState` 更明显适合线程化，因为 OCR 本身就是慢任务。

如果按原框架思路，有两种可能：

##### 方案 A：一个 ImgOCRState，内部一个工作线程串行处理所有图片

- `Enter()`：启动 OCR 工作线程
- 线程函数：遍历 `artifact.extracted_images`，逐张 OCR
- `Execute()`：检查进度、检查异常、判断是否完成
- `Exit()`：清理线程

这是最接近当前代码、改动最小的方案。

##### 方案 B：每张图片一个 OCR 子状态或子线程

也就是你说的：

```text
每个图片给一个 OCR 状态机线程去输出
```

这个思路在架构上是成立的，但要注意两个现实问题：

1. PaddleOCR 引擎通常比较重，不适合无节制地“一图一个新引擎”
2. 多张图片并发 OCR 需要考虑 GPU/CPU 资源竞争、模型初始化开销、缓存共享问题

所以更合理的折中通常是：

```text
一个 ImgOCRState 管一个共享 OCR 引擎
内部线程池或工作队列分发多张图片任务
```

而不是最粗暴的“每张图一个完整 OCR 状态机实例 + 一个新引擎”。

#### 5.3 Enter 和 Exit 在这里应该发挥什么作用

如果走线程化改造，`Enter/Execute/Exit` 应该恢复成真正的生命周期：

- `Enter()`：创建队列、事件、线程、初始化运行标志
- `Execute()`：主线程里只检查任务进度、产出事件、错误状态、完成条件
- `Exit()`：停止线程、释放资源、清理事件、保证状态退出后没有残留后台任务

这才符合当前 `state` 目录整体的原始风格。

### 6. 我对现状的判断

现在的 `state_parse.py` 不是“错”，它是：

```text
业务上能工作，但风格上更像同步业务函数，而不是这个项目原生的事件驱动状态机。
```

所以如果以后你要继续按老板或原框架思路往下重构，最合理的方向不是继续往 `Execute()` 里堆同步逻辑，而是：

```text
把耗时 I/O 和 OCR 放回子线程，把主线程状态恢复成调度和轮询者。
```

---

## 三、老板让你去看 openclaw 和 harness 的提示词，这件事该怎么做

老板原话是：

```text
结合昨天的智能体培训内容，空了去看一下 openclaw 和 harness，去研究一下他们的提示词。
```

这句话表面上是在让你“看看提示词”，但真实意图不只是看文案本身。

### 1. 老板真正想让你学什么

我判断老板真正想让你看的是这几层：

#### 1.1 智能体不是单条 prompt，而是一整套 prompt 组织方式

老板不是只想让你背某一句 system prompt，而是想让你理解：

```text
一个能工作的智能体，到底是如何组织它的提示词体系的。
```

比如：

- system prompt 怎么定角色
- planner prompt 怎么拆任务
- tool prompt 怎么约束工具调用
- memory prompt 怎么注入上下文
- evaluator prompt 怎么做结果校验

#### 1.2 提示词要和状态、工具、执行框架绑定起来看

昨天你们培训的“智能体”很可能讲了这些东西：

- 角色
- 规划
- 记忆
- 工具
- 反思
- 执行循环

所以老板让你看 `openclaw` 和 `harness`，不是为了单独欣赏 prompt 文案，而是要你学：

```text
提示词是如何嵌进 agent 框架里的。
```

#### 1.3 为你们自己的系统做参考

结合当前项目里 `state_solver.py` 的方向，我判断老板的意图还有一层：

```text
让你以后能把“提示词工程”接到你们自己的状态机 / task / planner 框架里。
```

也就是说，这不是纯研究任务，而是给后续产品实现做方法储备。

### 2. 你应该怎么着手

不要一上来就读几千行 prompt 正文。正确顺序应该是：

#### 第一步：先看它的整体架构，不要先看具体 prompt 词句

你先问自己：

```text
这个项目里，prompt 是在什么时候被调用的？
谁来调用？
调用完结果给谁？
```

要先把这些关系画出来：

```text
用户输入
 -> system prompt
 -> planner / orchestrator
 -> tool call / action selection
 -> observation
 -> next step prompt
 -> final answer
```

也就是说，先看：

- prompt 文件在哪里
- 哪个模块在加载 prompt
- prompt 输入变量是什么
- prompt 输出要求是什么
- 输出结果进入了哪个执行环节

#### 第二步：按“功能类型”给 prompt 分类

不要把所有提示词混着看，建议分类：

- 角色定义类 prompt
- 任务规划类 prompt
- 工具调用类 prompt
- 记忆整理类 prompt
- 结果评审类 prompt
- 错误恢复 / retry 类 prompt

你看的时候，每找到一个 prompt，都先标：

```text
这个 prompt 属于哪一类功能？
```

这样不会陷入“看了很多字但没形成结构”的状态。

#### 第三步：看 prompt 的输入变量和输出格式

这个很重要。

真正工程里，prompt 最值得学的不是华丽措辞，而是：

```text
输入变量怎么设计
输出格式怎么约束
```

你要重点看：

- 它接收了哪些上下文变量
- 是否有显式占位符
- 是否要求 JSON 输出
- 是否规定思考步骤
- 是否要求工具调用格式

因为这些东西最容易迁移到你们自己的系统里。

#### 第四步：看它如何处理失败和不确定性

成熟的 agent 提示词，不只是“顺利时怎么说”，更重要的是：

- 不确定时怎么表述
- 缺信息时怎么提问
- 工具失败时怎么恢复
- 输出不合规时怎么重试

你要专门观察有没有这种设计。

#### 第五步：最后才总结它的写法风格

比如：

- 是命令式风格还是规则式风格
- 是长 system prompt 还是多段小 prompt
- 是否大量使用 few-shot 示例
- 是否强调边界、禁止项、输出 schema

这一步是“风格总结”，不是最开始就做的事。

### 3. 如果你真的去研究，建议你带着这些问题看

你可以直接带着下面这些问题去看 `openclaw` 和 `harness`：

1. 它的核心 agent 循环是什么？
2. prompt 是挂在哪个循环节点上的？
3. 哪些 prompt 负责规划，哪些负责执行，哪些负责校验？
4. prompt 的输入变量有哪些？这些变量是谁提供的？
5. prompt 的输出是自由文本、结构化 JSON，还是工具调用指令？
6. 它如何降低模型乱答、越权、格式失控的概率？
7. 它如何处理工具失败、信息不足、任务中断？
8. 它的 prompt 设计里，哪些东西可以迁移到你们当前系统？

### 4. 你当前项目里和这件事最相关的线索

虽然当前仓库里没有一个明确叫 `harness` 的本地实现，但已经有 `openclaw` 相关线索，主要在：

```text
docs/参考Claw_README.md
state/state_solver.py
```

`state_solver.py` 里已经有非常接近 prompt 框架的雏形：

- `PromptIndex`
- `_init_prompts()`
- `execute_prompt_task()`

这说明你们系统本身就准备走：

```text
提示词文件索引 -> 根据任务加载 prompt -> 注入输入变量 -> 模型执行 -> 解析结构化输出
```

所以老板让你研究 `openclaw` / `harness`，很可能不是旁观学习，而是：

```text
让你借鉴成熟 agent 提示词体系，反过来完善你们自己的 prompt 执行框架。
```

### 5. 老板的意图，我的直白判断

如果用一句话总结老板的意图，我会这么说：

```text
老板不是要你“抄几段 prompt”，而是要你学会：成熟智能体系统是如何把提示词、工具、状态、任务执行循环组织成一个工程系统的。
```

再具体一点：

- 让你建立“prompt 不是文案，而是系统接口”的意识
- 让你从训练课里的概念，落到真实项目结构上
- 让你以后能参与你们自己 agent 的 prompt 设计、调试和架构接线

### 6. 你应该交付什么样的研究结果

如果以后老板真让你汇报，不要只说：

```text
我看了，感觉 prompt 写得挺好。
```

这种没有价值。

更好的汇报结构应该是：

1. `openclaw` / `harness` 的 agent 结构概览
2. 它们的 prompt 分类
3. 每类 prompt 的输入、输出、作用点
4. 它们如何约束模型输出与工具调用
5. 哪些设计值得迁移到我们当前系统
6. 如果落到当前仓库，最先能改的是哪一块

这样老板会觉得你不是“看过了”，而是“已经开始具备工程迁移视角了”。

---

## 最后一句话

你现在这三个问题，其实都指向同一个能力：

```text
从“代码长什么样”提升到“系统为什么这样设计，以及以后应该怎么演化”。
```

标题栈是在学数据结构和落盘方式。

`state_parse.py` 和整个 `state` 风格差异，是在学架构一致性和生命周期设计。

`openclaw` / `harness` 提示词研究，是在学 agent 系统里 prompt 如何成为工程接口。

如果你能把这三块都看懂，你后面接这类任务就不会只是“会改代码”，而会开始进入“会看系统、会拆方向、会接架构”的阶段。
