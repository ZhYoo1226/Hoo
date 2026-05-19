# PDDL文档

# l2p : LLM-driven Planning Model library kit

This library is a collection of tools for PDDL model generation extracted from natural language driven by large language models. This library is an expansion from the survey paper [**LLMs as Planning Formalizers: A Survey for Leveraging Large Language Models to Construct Automated Planning Specifications**](https://arxiv.org/abs/2503.18971v1).

L2P is an offline, natural language-to-planning model system that supports domain-agnostic planning. It does this via creating an intermediate [PDDL](https://planning.wiki/guide/whatis/pddl) representation of the domain and task, which can then be solved by a classical planner.

Full library documentation can be found: [**L2P Documention**](https://marcustantakoun.github.io/l2p.github.io/)

## Usage

This is the general setup to build domain predicates:
```python
import os
from l2p.llm.openai import OPENAI
from l2p.utils import load_file
from l2p.domain_builder import DomainBuilder

domain_builder = DomainBuilder()

api_key = os.environ.get('OPENAI_API_KEY')
llm = OPENAI(model="gpt-4o-mini", api_key=api_key)

# retrieve prompt information
base_path='tests/usage/prompts/domain/'
domain_desc = load_file(f'{base_path}blocksworld_domain.txt')
predicates_prompt = load_file(f'{base_path}formalize_predicates.txt')
types = load_file(f'{base_path}types.json')
action = load_file(f'{base_path}action.json')

# extract predicates via LLM
predicates, llm_output, validation_info = domain_builder.formalize_predicates(
    model=llm,
    domain_desc=domain_desc,
    prompt_template=predicates_prompt,
    types=types
    )

# format key info into PDDL strings
predicate_str = "\n".join([pred["raw"].replace(":", " ; ") for pred in predicates])

print(f"PDDL domain predicates:\n{predicate_str}")
```

Here is how you would setup a PDDL problem:
```python
from l2p.utils.pddl_types import Predicate
from l2p.task_builder import TaskBuilder

task_builder = TaskBuilder() # initialize task builder class

api_key = os.environ.get('OPENAI_API_KEY')
llm = OPENAI(model="gpt-4o-mini", api_key=api_key)

# load in assumptions
problem_desc = load_file(r'tests/usage/prompts/problem/blocksworld_problem.txt')
task_prompt = load_file(r'tests/usage/prompts/problem/formalize_task.txt')
types = load_file(r'tests/usage/prompts/domain/types.json')
predicates_json = load_file(r'tests/usage/prompts/domain/predicates.json')
predicates: list[Predicate] = [Predicate(**item) for item in predicates_json]

# extract PDDL task specifications via LLM
objects, init, goal, llm_response, validation_info = task_builder.formalize_task(
    model=llm,
    problem_desc=problem_desc,
    prompt_template=task_prompt,
    types=types,
    predicates=predicates
    )

# generate task file
pddl_problem = task_builder.generate_task(
    domain_name="blocksworld",
    problem_name="blocksworld_problem",
    objects=objects,
    initial=init,
    goal=goal)

print(f"### LLM OUTPUT:\n {pddl_problem}")
```

Here is how you would setup a Feedback Mechanism:
```python
from l2p.feedback_builder import FeedbackBuilder

feedback_builder = FeedbackBuilder()

api_key = os.environ.get('OPENAI_API_KEY')
llm = OPENAI(model="gpt-4o-mini", api_key=api_key)

problem_desc = load_file(r'tests/usage/prompts/problem/blocksworld_problem.txt')
types = load_file(r'tests/usage/prompts/domain/types.json')
feedback_template = load_file(r'tests/usage/prompts/problem/feedback.txt')
predicates_json = load_file(r'tests/usage/prompts/domain/predicates.json')
predicates: list[Predicate] = [Predicate(**item) for item in predicates_json]
llm_response = load_file(r'tests/usage/prompts/domain/llm_output_task.txt')

fb_pass, feedback_response = feedback_builder.task_feedback(
    model=llm,
    problem_desc=problem_desc,
    llm_output=llm_response,
    feedback_template=feedback_template,
    feedback_type="llm",
    predicates=predicates,
    types=types)

print("[FEEDBACK]\n", feedback_response)
```


## Installation and Setup
Currently, this repo has been tested for Python 3.11.10 but should be fine to install newer versions.

You can set up a Python environment using either [Conda](https://conda.io) or [venv](https://docs.python.org/3/library/venv.html) and install the dependencies via the following steps.

**Conda**
```
conda create -n L2P python=3.11.10
conda activate L2P
pip install -r requirements.txt
```

**venv**
```
python3.11.10 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

These environments can then be exited with `conda deactivate` and `deactivate` respectively. The instructions below assume that a suitable environemnt is active.

**API keys**

L2P requires access to an LLM. L2P provides support for OpenAI's models and other providers compatible with OpenAI SDK. To configure these, provide the necessary API-key in an environment variable.

**OpenAI**
```
export OPENAI_API_KEY='YOUR-KEY' # e.g. OPENAI_API_KEY='sk-123456'
```

Refer to [here](https://platform.openai.com/docs/quickstart) for more information.

**HuggingFace**

Additionally, we have included support for using Huggingface models. One can set up their environment like so:
```
parser = argparse.ArgumentParser(description="Testing HF usage")
parser.add_argument('-test_hf', action='store_true')
parser.add_argument("--model", type=float, required=True, help = "model name")
parser.add_argument("--model_path", type=str, required=True, help = "path to llm")
parser.add_argument("--config_path", type=str, default="l2p/llm/utils/llm.yaml", help = "path to yaml configuration")
parser.add_argument("--provider", type=str, default="huggingface", help = "backend provider")
args = parser.parse_args()

huggingface_model = HUGGING_FACE(model=args.model, model_path=args.model_path, config_path=args.config_path, provider=args.provider)
```

Users can refer to l2p/llm/utils/llm.yaml to better understand (and create their own) model configuration options, including tokenizer settings, generation parameters, and provider-specific settings.

**l2p/llm/base.py** contains an abstract class and method for implementing any model classes in the case of other third-party LLM uses.

## Planner
For ease of use, our library contains submodule [FastDownward](https://github.com/aibasel/downward/tree/308812cf7315fe896dbcd319493277d82aa36bd2). Fast Downward is a domain-independent classical planning system that users can run their PDDL domain and problem files on. The motivation is that the majority of papers involving PDDL-LLM usage uses this library as their planner.

**IMPORTANT** FastDownward is a submodule in L2P. To use the planner, you must clone the GitHub repo of [FastDownward](https://github.com/aibasel/downward/tree/308812cf7315fe896dbcd319493277d82aa36bd2) and run the `planner_path` to that directory.

Here is a quick test set up:
```python
from l2p.utils.pddl_planner import FastDownward

# retrieve pddl files
domain_file = "tests/pddl/test_domain.pddl"
problem_file = "tests/pddl/test_problem.pddl"

# instantiate FastDownward class
planner = FastDownward(planner_path="<PATH_TO>/downward/fast-downward.py")

# run plan
success, plan_str = planner.run_fast_downward(
    domain_file=domain_file,
    problem_file=problem_file,
    search_alg="lama-first"
)

print(plan_str)
```

To stay up to date with the most current papers, please visit [**here**](https://marcustantakoun.github.io/l2p.github.io/paper_feed.html).

## Contact
Please contact `20mt1@queensu.ca` for questions, comments, or feedback about the L2P library.



# PDDL理解

作为后续创建领域规范的基础，理解“任务”在PDDL中的含义至关重要。

下面我将从**基本定义**、**基本要素**、**前置信息与假设**、以及**建模的逻辑步骤**这四个方面，为你详细拆解。

---

### 一、 任务的基本定义

PDDL 是 **Planning Domain Definition Language**（规划领域定义语言）的缩写。

简单来说，它是一种**形式化建模语言**，专门用来描述人工智能中的**自动规划**问题。它的核心作用是让人类能把一个规划问题（比如让机器人把箱子从A点搬到B点）用一种计算机（尤其是规划器，Planner）能理解和求解的标准化方式写出来。

一个典型的PDDL描述会分为两个文件：

1.  **领域文件 (Domain File)**：定义“规则和工具”。包含所有可能的**动作**（Actions，如“拿起”、“放下”、“移动”）以及每个动作的**前提条件**和**效果**。它描述的是问题所在的客观世界规律。
2.  **问题文件 (Problem File)**：定义“具体任务”。描述某个特定场景下的初始状态（如“机器人站在房间1，箱子和苹果在房间2”）和**目标状态**（如“苹果在房间3”）。

**主要用途**：

-   **国际规划竞赛**：作为标准语言，让不同规划器公平比拼。
-   **机器人控制**：描述机器人动作和任务目标，自动生成动作序列。
-   **流程自动规划**：如工厂生产调度、供应链管理、太空任务规划（NASA曾用于火星车）。
-   **游戏AI**：描述游戏规则，自动寻找过关或完成任务的动作序列。

**核心特点**：

-   **声明式**：你只需描述“是什么”（初始状态、目标、动作逻辑），不用告诉计算机“怎么做”。
-   **逻辑基础**：基于一阶谓词逻辑，用谓词（如`(at robot room1)`）来描述世界状态。
-   **标准化**：有多种扩展版本（如处理时间、连续变化、概率的PDDL+）。

**一个极简例子**：

```lisp
;; 动作定义：拾起物体
(:action pickup
    :parameters (?obj)
    :precondition (and (at ?obj) (robot-free))  ; 前提：物体在面前，机器人空闲
    :effect (and (holding ?obj) (not (at ?obj)) (not (robot-free)))
)
```

**总结**：PDDL是连接“人类描述的问题”和“AI求解器”之间的桥梁语言。当你想让一个AI系统自动规划出完成一系列目标的最优动作序列时，就可以用它来建模。



在PDDL（规划领域定义语言）中，**任务（Task）** 通常指一个**规划问题（Planning Problem）**。

它的核心定义是：**给定一个世界状态的初始描述、一个期望达成的目标描述，以及一套可用的动作模型，找到一个动作序列（即规划），使得从初始状态开始，依次执行这些动作后，能够达到目标状态。**

简单来说，PDDL的任务就是回答一个问题：“**为了从A状态到达B状态，我应该按什么顺序执行哪些操作？**”

PDDL将任务描述严格分为两个文件：
1.  **领域文件（Domain File）**：定义了“规则”和“能力”。即有哪些动作（Action），每个动作的前置条件（Preconditions）和效果（Effects）。这是通用的，可以用于解决多个不同的问题。
2.  **问题文件（Problem File）**：定义了“具体场景”。即有哪些对象（Objects），世界的初始状态（Initial State），以及想要达成的目标（Goal）。

---

### 二、 任务的基本要素（PDDL建模的四个核心）

无论是领域文件还是问题文件，任何一个PDDL任务都包含以下四个基本要素：

#### 1. 对象
-   **定义**：问题世界中的个体或实体。
-   **例子**：机器人、房间、箱子、桌子、城市A、城市B。
-   **在PDDL中**：在 `:objects` 字段中定义，通常带有类型（如 `robot - Robot`）。

#### 2. 谓词
-   **定义**：描述世界状态的关系或属性。它是逻辑上的“事实”。状态要么为真，要么为假。
-   **在PDDL中**：在领域文件的 `:predicates` 字段中定义。
-   **例子**：
    -   `(at robot room1)` —— 机器人在房间1。
    -   `(connected room1 room2)` —— 房间1和房间2相连。
    -   `(holding ?r - Robot ?b - Box)` —— 机器人r拿着箱子b。

#### 3. 动作
-   **定义**：改变世界状态的操作。这是规划器唯一可以调用的“函数”。
-   **结构**：
    -   **参数**：动作涉及的对象。
    -   **前置条件**：执行该动作前，世界必须满足的条件（逻辑与、或、非）。
    -   **效果**：动作执行后，世界发生的变化（添加事实、删除事实）。

#### 4. 状态
-   **定义**：世界在某一时刻的快照。
    -   **初始状态**：问题开始时的状态。
    -   **目标状态**：问题结束时需要满足的状态（通常是一组必须为真的谓词集合）。

---

### 三、 前置信息与假设

在你开始用PDDL描述一个任务之前，需要先明确以下**前置信息**。这些信息决定了模型的精确性和规划器能否成功求解。

1.  **封闭世界假设**
    -   PDDL默认采用封闭世界假设。即在初始状态中**未明确声明为真**的事实，都被认为是**假**的。这意味着你不需要列出所有为假的事实，只需要列出“世界初始时有哪些事情正在发生”。

2.  **对象与类型的划分**
    -   你需要理清世界中的实体分类。例如，区分`物理对象`和`位置`，或者区分`车辆`和`包裹`。清晰的类型系统可以减少无效搜索，避免规划器产生荒谬的动作（例如试图把“一个城市”放进“一个箱子”里）。

3.  **动作的原子性**
    -   在PDDL中，动作被视为瞬间发生的、不可分割的原子操作。如果你需要描述“移动机器人需要3秒”，或者“两个动作同时发生”，那就属于时序规划或数值规划，需要用到PDDL 2.0以上的扩展特性。经典PDDL（1.2）不考虑时间和并发。

4.  **确定性**
    -   经典PDDL假设世界是确定性的。即，如果一个动作被执行，它的效果是100%会发生的，不存在“移动机器人可能失败”的概率性情况。

---

### 四、 建模的逻辑步骤

将自然语言描述转化为PDDL任务，通常遵循以下逻辑步骤。我们可以用一个简单的例子来同步说明：**“有一个机器人和两个房间。机器人初始在房间A。它需要移动到房间B。”**

#### 步骤1：定义对象与类型
首先，分析问题中存在的实体。
-   **逻辑**：找出名词。如果有多个同类名词，抽象为类型。
-   **输出**：
    -   类型：`Location`
    -   对象：`roomA`， `roomB`
    -   类型：`Robot`
    -   对象：`robot1`

#### 步骤2：定义谓词（世界的“语法”）
思考世界中存在哪些“关系”是规划器需要知道的。
-   **逻辑**：
    -   机器人是否在某个位置？ -> `(at ?r - Robot ?l - Location)`
    -   两个位置是否连通？（移动的合法性） -> `(connected ?l1 - Location ?l2 - Location)`

#### 步骤3：定义动作（能力的“逻辑”）
定义机器人如何改变世界。
-   **逻辑**：
    -   动作名称：`move`
    -   **参数**：机器人 `?r`，从 `?from` 出发，去往 `?to`。
    -   **前置条件**：
        1.  机器人当前在 `?from`：`(at ?r ?from)`
        2.  `?from` 和 `?to` 是连通的：`(connected ?from ?to)`
    -   **效果**：
        1.  添加：机器人现在在 `?to`：`(at ?r ?to)`
        2.  删除：机器人不再在 `?from`：`(not (at ?r ?from))`

#### 步骤4：定义初始状态（具体事实）
在问题文件中，实例化具体的世界。
-   **逻辑**：
    -   `(at robot1 roomA)`
    -   `(connected roomA roomB)`
    -   `(connected roomB roomA)` （通常连通性是无向的）

#### 步骤5：定义目标状态（约束条件）
明确需要达成的条件。
-   **逻辑**：
    -   `(at robot1 roomB)`

#### 步骤6：生成规划
将上述文件输入规划器后，规划器会进行**前向搜索**或**后向搜索**：
1.  查看初始状态：机器人是否在目标？否。
2.  查看哪些动作可用：`move` 动作的 `?from` 绑定为 `roomA`，`?to` 绑定为 `roomB`。
3.  检查前置条件：`(at robot1 roomA)` 为真，`(connected roomA roomB)` 为真。
4.  执行动作，应用效果（删除 `at roomA`，添加 `at roomB`）。
5.  检查新状态是否满足 `(at robot1 roomB)`。满足，规划结束。
6.  **输出**：`(move robot1 roomA roomB)`

---

### 总结

在PDDL中，**任务**的本质是一个**逻辑推理问题**。

-   **基本定义**：从初始状态到目标状态的动作序列搜索。
-   **核心要素**：**对象**（谁）、**谓词**（什么关系）、**动作**（怎么变）、**状态**（起点与终点）。
-   **关键逻辑**：建模时，需要先抽象出类型和谓词（领域），再填充具体实例和初始条件（问题），最后通过定义动作的前置与效果，来构建状态空间的转移图。

如果你现在有一个具体的自然语言场景需要转化为PDDL规范，可以把场景描述发给我，我们可以按照上述步骤一起进行拆解和建模。







