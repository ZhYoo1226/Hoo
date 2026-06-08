import json
import os
import re

import requests
import yaml
from bs4 import BeautifulSoup
from engram import Engram

from openai import OpenAI

from common import GlobalFunction

with open("../../config.yaml", "r", encoding="utf-8") as f:
    g_yaml_config = yaml.safe_load(f)

client = OpenAI(
    api_key=os.getenv("API_KEY") or g_yaml_config["openai"]["api_key"],
    base_url=g_yaml_config["openai"]["base_url"],
)

import sqlite3

# 核心：确保 path 参数是有效的本地文件路径
memory = Engram(path="./task_models.engram")




def build_prompt(task_desc, similar_template=False):
    base_instruction = """
[:角色]
你是一个专业的任务分析与信息需求专家。你的任务是根据用户提供的任务描述，逆向推导出完成该任务所需的所有关键信息、前置条件和依赖关系。
[:要求说明]
1. 从“最终目标”出发，逆向思考：为了达到这个目标，必须事先知道什么信息、具备什么条件。
2. required_info 中的每一项都应是原子化的。
3. 对于涉及认证、权限的任务，必须包含对应的凭证信息。
4. 如果信息项可以通过其他信息推导或获取，请在 source_hint 中注明。
5. 区分静态信息和动态信息（type 字段可辅助判断）。
6. 如果任务需要特定工具或软件，请在 dependencies 中列出。

最终答案以“### INFORMATION_REQ”开头，后跟Python字典对“{'name':'description'}”。内容必须用``` ```注释块括中，如下所示：
### INFORMATION_REQ
```
{
  "task_name": "简短的任务名称",
  "task_description": "原始任务描述（由用户提供）",
  "goal": "最终目标的一句话描述",
  "prerequisites": ["前置条件1", "前置条件2", ...],
  "required_info": [
    {
      "name": "信息项名称",
      "description": "含义说明",
      "required": true/false,
      "type": "string|number|boolean|file_path|url|credential|...",
      "example": "示例值",
      "source_hint": "获取方式（可选）"
    }
  ],
  "dependencies": ["依赖项1", ...],
  "output": "任务执行成功后产生的成果描述",
  "estimated_effort": "低/中/高"
}
```
"""
    if similar_template:
        example = json.dumps(similar_template, ensure_ascii=False, indent=2)
        prompt = f"""
        {base_instruction}
        下面是一个已完成的任务模板示例，你可以参考其结构和风格：
        ```json
        {example}
        ```
        现在，请为以下新任务生成信息需求依赖：
        任务描述：{task_desc}
        """
    else:
        prompt = f"""
        {base_instruction}
        
        现在，请为以下任务生成信息需求依赖：
        任务描述：{task_desc}
        """
        return prompt


def find_similar_template(task_desc):
    # 使用语义搜索查找最相似的已存储模板
    results = memory.recall(task_desc, types=["task_template"], limit=1)
    if results:
        # 假设返回的记忆对象包含 content 字段（JSON字符串）
        return json.loads(results[0]["content"])
    return None


def generate_template(task_desc, similar_template=None):
    prompt = build_prompt(task_desc, similar_template)
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"}  # 如果API支持
    )
    content = response.choices[0].message.content
    print(f"llm_out:====>\n{content}")
    info_parsed = GlobalFunction.parse_llm_text_output_json(content)
    # info_parsed = ast.literal_eval(info_raw)
    return info_parsed


def enhance_with_docs(template, tech_keyword):
    # 示例：搜索 FastAPI 文档，提取参数
    url = f"https://fastapi.tiangolo.com/reference/"
    # 简单的网页解析，实际可更复杂
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    # 提取参数信息...
    # 将新发现的信息项合并到 template["required_info"] 中
    return template


def store_template(template, similar_id=None):
    # 生成唯一ID（可选）
    # 存储核心内容
    memory.add(
        content=json.dumps(template, ensure_ascii=False),
        type="task_template",
        importance=0.9,
        metadata={
            "task_name": template["task_name"],
            "task_description": template["task_description"],
            "version": 1,
            "similar_template_id": similar_id
        }
    )
    # 同时将 required_info 拆分为独立记忆，便于检索
    for info in template["required_info"]:
        memory.add(
            content=f"{template['task_name']} 需要 {info['name']}: {info['description']}",
            type="info_requirement",
            importance=1.0 if info["required"] else 0.5,
            metadata={
                "task_name": template["task_name"],
                "info_name": info["name"],
                "required": info["required"],
                "type": info["type"]
            }
        )
    print(f"Template for '{template['task_name']}' stored.")


def collect_feedback(task_name, missing_info, error_msg):
    # 将反馈存储为特殊类型的记忆
    memory.add(
        content=f"Task '{task_name}' execution feedback: missing {missing_info}, error: {error_msg}",
        type="execution_feedback",
        importance=0.7,
        metadata={
            "task_name": task_name,
            "missing_info": missing_info,
            "error": error_msg
        }
    )


'''
## 三、进阶特性：类比学习与元认知

### 类比学习（Cross-task Analogical Transfer）

当多个任务属于同一领域时，可以利用它们的模板共性，自动生成领域通用模板。例如，所有“数据下载”任务都包含服务器地址、认证信息等。
'''


def extract_common_info(templates):
    # 统计所有 required_info 中 name 的出现频率
    common = {}
    for tmpl in templates:
        for info in tmpl["required_info"]:
            common[info["name"]] = common.get(info["name"], 0) + 1
    # 取出现次数 > 阈值的作为通用信息项
    generic = [name for name, cnt in common.items() if cnt >= len(templates) * 0.7]
    return generic


def update_template(task_name, feedback_list):
    # 检索当前模板
    results = memory.recall(task_name, type="task_template", limit=1)
    if not results:
        return
    old_template = json.loads(results[0]["content"])

    # 构建优化提示词
    prompt = f"""
你是一个任务模板优化专家。现有以下任务模板：
```json
{json.dumps(old_template, indent=2)}
根据以下执行反馈（缺失信息或错误），请优化这个模板，补全缺失的信息项或修正不准确的内容。
反馈列表：
{json.dumps(feedback_list, indent=2)}

请输出优化后的完整 JSON 模板，格式与之前相同。
"""
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    new_template = json.loads(response.choices[0].message.content)
    memory.add(
        content=json.dumps(new_template, ensure_ascii=False),
        type="task_template",
        importance=0.9,
        metadata={
            "task_name": new_template["task_name"],
            "task_description": new_template["task_description"],
            "version": old_template.get("version", 0) + 1,
            "previous_version_id": results[0]["id"]
        }
    )
    print(f"Template updated to version {new_template['version']}")


def extract_json_from_response(llm_response):
    # 使用正则表达式提取 ```json ... ``` 之间的内容
    match = re.search(r'```json(.*?)```', llm_response, re.DOTALL)
    if match:
        json_content = match.group(1).strip()
        try:
            json_ret = json.loads(json_content)
            print(f"解析json结果为{json_ret}")
            return json_ret
        except json.JSONDecodeError as e:
            raise f"JSON 解析失败: {e}\n==>>>{llm_response}"
    else:
        raise f"未找到 JSON 内容：{llm_response}"


def self_evaluate(template):
    prompt = f"""
评估以下任务模板的质量，从完整性、准确性、可执行性三个维度打分（1-10），并给出改进建议。
模板：
{json.dumps(template, indent=2)}

输出JSON：
{{"completeness": 0-10, "accuracy": 0-10, "executability": 0-10, "suggestions": "..."}}
"""
    response = client.chat.completions.create(
        model="Qwen3-14B",
        messages=[{'role': 'user', 'content': prompt}],
        stream=False,
        temperature=0.7,
        max_tokens=2000,
        stream_options={"include_usage": True}
    )
    # 返回的不是标准格式JSON，需要提炼。
    try:
        eval_result = extract_json_from_response(response.choices[0].message.content)
        return eval_result
    except Exception as e:
        return {"completeness": 7, "accuracy": 7, "executability": 7, "suggestions": "评价失败，默认评价"}


# 根据任务，得到这个任务的过程描述。讲一个简单需求泛化为一个更详细的需求。
def auto_build_task_template(task_desc):
    # 1. 检索相似模板
    similar = find_similar_template(task_desc)
    similar_id = similar.get("id") if similar else None

    # 2. 生成初始模板（利用类比学习）
    template = generate_template(task_desc, similar)

    # 3. 自我评估，若质量不足则增强
    eval_score = self_evaluate(template)
    if eval_score["completeness"] < 7 or eval_score["accuracy"] < 7:
        # 触发外部知识增强
        # template = enhance_with_docs(template, extract_keyword(task_desc))
        pass

    # 4. 存储模板
    store_template(template, similar_id)

    # 5. 返回模板供后续使用
    return template


# ---------- 1. 生成任务列表 ----------
def generate_task_list(topic, count=20):
    prompt = f"""
你是一个任务策划专家。请根据以下主题，生成 {count} 个具体、可执行的任务。每个任务应描述清晰，涵盖该主题下的常见操作。

主题：{topic}

输出格式：每行一个任务描述，不要添加序号或额外解释。
"""
    response = client.chat.completions.create(
        model="Qwen3-14B",
        messages=[{'role': 'user', 'content': prompt}],
        stream=False,
        temperature=0.7,
        max_tokens=2000,
        stream_options={"include_usage": True}
    )
    tasks = response.choices[0].message.content.strip().split("\n")
    # 过滤空行
    tasks = [t.strip() for t in tasks if t.strip()]
    return tasks


def load_db_info():
    conn = sqlite3.connect("./task_templates.db")
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(memories)")
    columns = cursor.fetchall()
    for col in columns:
        print(col)

    cursor.execute("SELECT content,memory_type,metadata FROM memories")
    rows = cursor.fetchall()
    idx = 0
    for row in rows:
        idx += 1
        if idx > 10:
            print(idx)
            break
        print(f"memory_type={row[1]}，metadata：{row[2]}\n==>{row[0]}")
        try:
            content = json.loads(row[0])
            print(f"{content}")
        except Exception as e:
            print(e)
            pass
    conn.close()


def think_unkown(info: str = None):
    '''
    将用户输入的不清晰的任务，规划成一个成熟的任务。
    '''
    template = auto_build_task_template(task)
    print(json.dumps(template, indent=2, ensure_ascii=False))
    return template


def think_task(task: str = None):
    '''
    将用户输入的不清晰的任务，规划成一个成熟的任务。
    '''
    # 1. 检索相似模板
    similar = find_similar_template(task)
    similar_id = similar.get("id") if similar else None

    # 2. 生成初始模板（利用类比学习）
    template = generate_template(task, similar)

    # 3. 自我评估，若质量不足则增强
    eval_score = self_evaluate(template)
    if eval_score["completeness"] < 7 or eval_score["accuracy"] < 7:
        # 触发外部知识增强
        # template = enhance_with_docs(template, extract_keyword(task))
        pass

    # 4. 存储模板
    store_template(template, similar_id)

    # 5. 返回模板供后续使用
    return template


#
# # 遍历任务列表，为每个任务构建模板
# for task in generate_task_list("软件工程",50):
#     template = auto_build_task_template(task)
#     print(json.dumps(template, indent=2, ensure_ascii=False))
#
#
# for task in generate_task_list("网络工程",50):
#     template = auto_build_task_template(task)
#     print(json.dumps(template, indent=2, ensure_ascii=False))
#
#
# for task in generate_task_list("计算机",50):
#     template = auto_build_task_template(task)
#     print(json.dumps(template, indent=2, ensure_ascii=False))
#
#
# for task in generate_task_list("软件系统",50):
#     template = auto_build_task_template(task)
#     print(json.dumps(template, indent=2, ensure_ascii=False))
#
#
for task in generate_task_list("安全验证", 50):
    template = auto_build_task_template(task)
    print(json.dumps(template, indent=2, ensure_ascii=False))

#
# # 使用示例
# template = auto_build_task_template("SSH免密登录另一台服务器")
# print(json.dumps(template, indent=2,ensure_ascii=False))


# load_db_info()
