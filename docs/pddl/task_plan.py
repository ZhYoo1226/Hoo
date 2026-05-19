import json
import os
import subprocess
import tempfile
from typing import List, Dict, Any, Optional, Tuple

from l2p.domain_builder import DomainBuilder
from l2p.feedback_builder import FeedbackBuilder
# L2P 核心组件
from l2p.llm.openai import OPENAI
from l2p.task_builder import TaskBuilder
from l2p.utils import load_file
from l2p.utils.pddl_types import Predicate


# ========== 1. 定义 Engram 接口（需要你自己实现具体存储/检索） ==========
class EngramClient:
    """任务模板存储与检索的抽象接口"""

    def search(self, query: str) -> Optional[Dict[str, Any]]:
        """根据语义或关键字检索任务模板，返回结构示例：
        {
            "type": "domain" | "problem",
            "description": "获取 FTP 服务器端口号",
            "provides": ["has-port server1"],   # 该子任务能提供的事实
            "domain_desc": "...",               # 如果是 domain 模板，有领域描述
            "problem_desc": "..."               # 如果是 problem 模板，有问题描述
        }
        """
        # 实际实现中调用 Engram 的 API
        pass

    def store(self, key: str, template: Dict[str, Any]):
        """存储任务模板"""
        pass


# ========== 2. 规划器调用封装（以 Fast Downward 为例） ==========
def call_planner(domain_path: str, problem_path: str, fd_path: str) -> Tuple[bool, str]:
    """调用外部规划器，返回 (是否成功, 计划或错误信息)"""
    cmd = [fd_path, domain_path, problem_path, "--search", "astar(lmcut())"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if "Plan found" in result.stdout:
        return True, result.stdout
    else:
        # 提取错误信息供 FeedbackBuilder 使用
        error_msg = result.stderr if result.stderr else result.stdout
        return False, error_msg


# ========== 3. L2P 与 Engram 集成的主类 ==========
class L2PWithEngram:
    def __init__(self, api_key: str, engram_client: EngramClient, fast_downward_path: str):
        self.llm = OPENAI(model="gpt-4o-mini",
                          api_key=os.getenv("API_KEY") or g_yaml_config["openai"]["api_key"],
                          base_url=g_yaml_config["openai"]["base_url"],
                          )
        self.engram = engram_client
        self.fd_path = fast_downward_path

        # 初始化 L2P 组件
        self.domain_builder = DomainBuilder()
        self.task_builder = TaskBuilder()
        self.feedback_builder = FeedbackBuilder()

        # 缓存当前任务的 domain 和 predicates
        self.current_domain_str: Optional[str] = None
        self.current_predicates: List[Predicate] = []
        self.current_types: List[str] = []

    def plan(self, domain_desc: str, problem_desc: str, max_depth: int = 3) -> Dict[str, Any]:
        """
        主入口：从领域描述和问题描述开始，递归补全信息，返回最终计划。
        domain_desc: 领域自然语言描述（如 "FTP download domain"）
        problem_desc: 具体问题描述（如 "download file from 192.168.1.1"）
        """
        # 递归入口
        result = self._resolve(domain_desc, problem_desc, depth=0, max_depth=max_depth)
        return result

    def _resolve(self, domain_desc: str, problem_desc: str, depth: int, max_depth: int) -> Dict[str, Any]:
        if depth >= max_depth:
            return {"status": "max_depth_exceeded", "plan": None}

        # 步骤1：使用 DomainBuilder 提取 predicates 和 types（尝试从 Engram 检索或新建）
        domain_key = self._normalize_key(domain_desc)
        cached_domain = self.engram.search(domain_key)
        if cached_domain and cached_domain.get("type") == "domain":
            # 使用缓存
            self.current_types = cached_domain["types"]
            self.current_predicates = [Predicate(**p) for p in cached_domain["predicates"]]
            self.current_domain_str = cached_domain["domain_str"]
        else:
            # 调用 L2P 新建
            # 实际使用时需要加载 prompts（参考官方示例）
            # 这里简化流程，假设有函数 get_prompts() 返回所需模板内容
            prompts = self._get_domain_prompts()  # 需要你根据实际路径准备
            predicates, llm_output, val_info = self.domain_builder.formalize_predicates(
                model=self.llm,
                domain_desc=domain_desc,
                prompt_template=prompts["predicates_template"],
                types=prompts["types"]  # 这里 types 是 JSON 字符串或列表
            )
            # 转换为内部格式
            self.current_predicates = [Predicate(**p) for p in predicates]
            self.current_types = prompts["types"]  # 假设是列表
            # 生成 domain 字符串（你可以根据 predicates 生成，也可直接保存 LLM 原始输出）
            self.current_domain_str = self._build_domain_str(domain_desc, self.current_predicates, self.current_types)
            # 存入 Engram
            self.engram.store(domain_key, {
                "type": "domain",
                "types": self.current_types,
                "predicates": predicates,
                "domain_str": self.current_domain_str
            })

        # 步骤2：使用 TaskBuilder 生成具体问题（同样尝试缓存）
        problem_key = self._normalize_key(problem_desc)
        cached_problem = self.engram.search(problem_key)
        if cached_problem and cached_problem.get("type") == "problem":
            problem_str = cached_problem["problem_str"]
            objects = cached_problem["objects"]
            init = cached_problem["init"]
            goal = cached_problem["goal"]
        else:
            prompts = self._get_problem_prompts()  # 需要你准备
            objects, init, goal, llm_response, val_info = self.task_builder.formalize_task(
                model=self.llm,
                problem_desc=problem_desc,
                prompt_template=prompts["task_template"],
                types=self.current_types,
                predicates=self.current_predicates
            )
            # 生成 PDDL problem 字符串
            problem_str = self.task_builder.generate_task(
                domain_name="my_domain",  # 可动态命名
                problem_name="my_problem",
                objects=objects,
                initial=init,
                goal=goal
            )
            self.engram.store(problem_key, {
                "type": "problem",
                "objects": objects,
                "init": init,
                "goal": goal,
                "problem_str": problem_str
            })

        # 步骤3：写入临时文件，调用规划器
        with tempfile.TemporaryDirectory() as tmpdir:
            domain_path = f"{tmpdir}/domain.pddl"
            problem_path = f"{tmpdir}/problem.pddl"
            with open(domain_path, "w") as f:
                f.write(self.current_domain_str)
            with open(problem_path, "w") as f:
                f.write(problem_str)

            success, output = call_planner(domain_path, problem_path, self.fd_path)

            if success:
                return {"status": "success", "plan": output, "domain": self.current_domain_str, "problem": problem_str}

            # 步骤4：规划失败，使用 FeedbackBuilder 分析缺口
            # 需要准备 feedback 模板（参考官方示例）
            feedback_template = self._get_feedback_template()
            # 注意：这里需要将规划器的错误信息作为 llm_output 的替代或补充
            # 实际 L2P 的 task_feedback 接收的是之前 LLM 生成的输出，但我们这里没有单独的 llm_output，
            # 可以将 problem_str 作为 llm_output，并附上规划器错误。
            # 为简化，我们构建一个模拟的 llm_output 字符串
            simulated_llm_output = problem_str + "\n\nPLANNER_ERROR:\n" + output
            fb_pass, feedback_response = self.feedback_builder.task_feedback(
                model=self.llm,
                problem_desc=problem_desc,
                llm_output=simulated_llm_output,
                feedback_template=feedback_template,
                feedback_type="llm",  # 或 "user"
                predicates=self.current_predicates,
                types=self.current_types
            )

            # 解析反馈响应，提取缺失的信息（可能需要自定义解析逻辑）
            missing_info = self._extract_missing_from_feedback(feedback_response)

            # 步骤5：对每个缺失信息，尝试从 Engram 检索子任务
            new_facts = {}
            for missing in missing_info:
                sub_task = self.engram.search(missing)
                if sub_task and sub_task.get("type") == "problem":
                    # 递归调用子任务，获得其能提供的事实（如 provides 字段）
                    sub_result = self._resolve(
                        sub_task.get("domain_desc", domain_desc),
                        sub_task["problem_desc"],
                        depth + 1,
                        max_depth
                    )
                    if sub_result["status"] == "success":
                        # 假设子任务模板中指明了它能提供的事实
                        provides = sub_task.get("provides", [])
                        for fact in provides:
                            new_facts[fact] = True
                else:
                    # 无子任务模板，标记为需要用户交互（此处可调用用户输入函数）
                    user_input = input(f"请提供缺失信息：{missing} ")
                    # 将用户输入转换为事实，格式需与 predicates 一致
                    new_facts[missing] = user_input

            # 步骤6：如果有新获得的事实，则将其合并到原问题描述中，重新调用 _resolve
            if new_facts:
                # 将新事实加入 problem_desc，例如在描述末尾追加
                enriched_problem_desc = problem_desc + "\n已知信息：" + json.dumps(new_facts)
                return self._resolve(domain_desc, enriched_problem_desc, depth, max_depth)
            else:
                # 无法补全
                return {"status": "missing_info", "missing": missing_info, "feedback": feedback_response}

    # ========== 辅助方法 ==========
    def _normalize_key(self, text: str) -> str:
        """生成用于 Engram 检索的键（例如取前50个字符）"""
        return text[:50].strip()

    def _get_domain_prompts(self):
        """加载 domain 构建所需的模板（需根据你的实际路径调整）"""
        base = "tests/usage/prompts/domain/"
        return {
            "predicates_template": load_file(f"{base}formalize_predicates.txt"),
            "types": load_file(f"{base}types.json")  # 可能是 JSON 字符串
        }

    def _get_problem_prompts(self):
        """加载 problem 构建所需的模板"""
        base = "tests/usage/prompts/problem/"
        return {
            "task_template": load_file(f"{base}formalize_task.txt")
        }

    def _get_feedback_template(self):
        """加载反馈模板"""
        return load_file("tests/usage/prompts/problem/feedback.txt")

    def _build_domain_str(self, domain_desc: str, predicates: List[Predicate], types: List[str]) -> str:
        """根据提取的 predicates 和 types 生成 PDDL domain 字符串"""
        # 简化实现，实际可能需要更复杂的格式化
        types_str = " ".join(types)
        predicates_str = "\n  ".join([f"({p.name} {' '.join(p.args)})" for p in predicates])
        return f"""(define (domain my_domain)
  (:requirements :typing)
  (:types {types_str})
  (:predicates
    {predicates_str}
  )
)"""

    def _extract_missing_from_feedback(self, feedback_response: str) -> List[str]:
        """从反馈文本中提取缺失信息列表，例如提取 "has-port server1" 这样的谓词"""
        # 这是一个简单示例，实际可能需要更复杂的 NLP 或关键词匹配
        # 假设反馈中会包含类似 "Missing: (has-port server1)" 的句子
        import re
        missing = re.findall(r"Missing:\s*\((\w+\s+\w+)\)", feedback_response)
        return missing
