import json
import os

from action.functions import read_file
from common import GlobalFunction
from state import BaseState


class RefactorTableState(BaseState):
    """
    基于表头字段重构原始表格数据的状态。

    """

    stateName = "RefactorTableState"

    def __init__(self, source_md_path: str, table_headers: list = None, **kwargs):
        """
        构造方法，创建这个状态时自动调用的初始化函数。

        Args:
            source_md_path: 原始表格 markdown 文件的完整路径
            table_headers: 表头字段列表
            **kwargs: 通配关键字参数
        """

        super().__init__(**kwargs)

        self.table_content = None
        self.source_md_path = source_md_path
        self.table_headers = table_headers or []
        self.src_doc_name = os.path.basename(source_md_path)
        self.output_path = None
        self.refactored_content = None

        self.table_text = "(未提供)"

    def Enter(self, owner):
        """
        状态进入时调用的方法。
        """

        self.output_path = GlobalFunction.file_to_parse_path(self.source_md_path)
        os.makedirs(self.output_path, exist_ok=True)

    def Execute(self, owner):
        """
        状态的核心执行逻辑，状态机每帧都会调用这个方法。

        Args:
            owner: SolverOwner 对象，拥有 LLM 模型和其他资源
        """

        # 步骤1：检查源文件是否存在
        if not os.path.exists(self.source_md_path):
            print(f"源文件不存在: {self.source_md_path}")
            owner.remove_state(self)
            return

        # 步骤2：读取原始表格内容
        content = read_file(self.source_md_path)
        # print("文件内容为：" + content)

        self.table_content = content
        if not content:
            print(f"源文件内容为空: {self.source_md_path}")
            owner.remove_state(self)
            return

        # 步骤3：调用 LLM 执行提示词
        action_json = owner.execute_prompt("基于字段抽取JSON数据", state=self)

        # 步骤4：保存 LLM 返回的结果
        output_file = os.path.join(
            self.output_path,
            f"{os.path.splitext(self.src_doc_name)[0]}_重构.md"
        )

        # 处理 action_json，保存为 Markdown 中的 JSON 代码块
        if action_json and isinstance(action_json, dict):
            # 格式化 JSON 并保存为 ```json 代码块
            formatted_json = json.dumps(action_json, ensure_ascii=False, indent=2)
            content = f"```json\n{formatted_json}\n```"
        elif isinstance(action_json, list) and len(action_json) > 0:
            # 如果返回的是列表，包装为对象
            formatted_json = json.dumps({"data": action_json}, ensure_ascii=False, indent=2)
            content = f"```json\n{formatted_json}\n```"
        else:
            print(f"警告: LLM 返回结果为空或格式异常: {action_json}")
            content = ""

        if content:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"重构表格已保存: {output_file}")
        else:
            print(f"未能保存重构结果")

        # 步骤5：结束状态
        owner.remove_state(self)


def Exit(self, owner):
    """
    状态退出时调用的方法。
    """
    pass
