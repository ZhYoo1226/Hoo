import ast
import inspect
import io
import json
import logging
import os
import re
import textwrap
import tokenize
from datetime import datetime
from typing import List, Union

from . import PromptIndex, ChatMessage
from .config import g_yaml_config

"""存在的意义就是统一全局的变量名，哪些用了，哪些没用"""
GlobalNames = {
    "元认知分类": "元认知分类",
    "领域模型": "领域模型",
    "规划任务下一步动作": "规划任务下一步动作",
    "任务提示词": "任务提示词",
}


class GlobalObject:
    """
    全局对象，用于存储全局变量
    全局变量以首字母大写
    下划线开头，不对外。
    """
    _WorkFileMap: dict = {}  # uuid → WorkFile
    Prompts: PromptIndex = None


class GlobalFunction:

    @staticmethod
    def parse_chat_text_to_messages(md_chat_text: str) -> List[ChatMessage]:
        # 按行首的 "[xxx]: " 切分，保留分隔符本身
        # (?=...) 是正向预查，匹配位置但不消费字符，从而保留标记在结果中
        pattern = re.compile(r'(?=^\[[^\]]+]: )', re.MULTILINE)
        parts = pattern.split(md_chat_text)
        # split 会产生一个空字符串在开头（如果文本以标记开头），过滤掉
        # 判断role是否为None，删除None
        chat_messsages = [m for m in [ChatMessage(data=p.strip().rstrip('\n')) for p in parts if p] if m.role is not None]
        return chat_messsages


    @staticmethod
    def register_work_files(df: "pd.DataFrame"):
        """从 files_abstract.parquet 的 DataFrame 注册 WorkFile 对象"""
        from .entities import WorkFile
        # 需要清空之前的注册吗？
        GlobalObject._WorkFileMap.clear()
        for _, row in df.iterrows():
            wf = WorkFile(row.to_dict())
            GlobalObject._WorkFileMap[wf.uuid] = wf

    @staticmethod
    def show_file_structure():
        """
        显示文件的目录结构
        """
        workspace = "./workspace"
        file_path = "./workspace/user/files/test/test.xlsx"
        base_path = "./workspace/user/files/test"
        log_text = ["\n"]
        log_text.append(f"workspace: {workspace}")
        log_text.append(f"file_path: {file_path}")
        log_text.append(f"base_path: {base_path}")
        log_text.append(f"|--{os.path.basename(file_path)}")
        log_text.append(f"|--{GlobalFunction.file_to_parse_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_full_text_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_preview_text_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_doc_text_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_doc_table_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.file_to_doc_image_path(file_path)[len(base_path) + 1:]}")
        log_text.append(f"|----{GlobalFunction.memory_path()}")
        return "\n".join(log_text)

    @staticmethod
    def init_paths():
        '''初始化系统所有的文件目录'''
        for p in g_yaml_config["app"]["paths"].values():
            p = os.path.abspath(os.path.join(GlobalFunction.workspace_path(), p))
            if p and not os.path.exists(p):
                os.makedirs(p, exist_ok=True)  # 递归创建，父目录不存在也会创建

    ################目录创建##################
    @staticmethod
    def workspace_path()->str:
        return g_yaml_config["app"]["workspace_path"]

    @staticmethod
    def task_path():
        """任务路径目录"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["task_path"]))

    @staticmethod
    def chat_path():
        """聊天记录存储路径目录"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["chat_path"]))

    @staticmethod
    def domain_model_path():
        """重要：用户的工作或生活的行为模型，由AI总结生成。"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["domain_model_path"]))

    @staticmethod
    def log_path():
        """日志存储路径目录"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["log_path"]))

    @staticmethod
    def prompt_path():
        """提示词存储路径目录"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["prompt_path"]))

    @staticmethod
    def memory_path():
        """重要：记忆存储路径，不可删除。需要备份。"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["paths"]["memory_path"]))

    @staticmethod
    def knowledge_path(name: str) -> str:
        """知识树产物目录：workspace/knowledge/{name}/"""
        return os.path.join(GlobalFunction.workspace_path(), "knowledge", name)

    @staticmethod
    def review_path(plan_type: str) -> str:
        """审查产物目录：workspace/review/{plan_type}/"""
        return os.path.join(GlobalFunction.workspace_path(), "review", plan_type)

    ################文件##################

    @classmethod
    def files_abstract_path(cls):
        return os.path.join(GlobalFunction.workspace_path(), "user", "files", "files_abstract.parquet")

    @staticmethod
    def user_profile_md():
        """重要：记忆存储路径，不可删除。需要备份。"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["files"]["user_profile_path"]))

    @staticmethod
    def agent_profile_md():
        """重要：智能体的SOUL.md文件路径"""
        return os.path.abspath(os.path.join(GlobalFunction.workspace_path(), g_yaml_config["app"]["files"]["agent_profile_path"]))

    ########################################################
    @staticmethod
    def file_to_dir_name(file_path: str):
        """
        获取文件名字,后缀符号[点]替换为下划线.
        path= ./workspace/user/files/test/test.xlsx
        return= test_xlsx
        """
        return os.path.basename(file_path).replace(".", "_")

    @staticmethod
    def file_to_name_without_suffix(file_path: str):
        """获取文件名字,不包含后缀
        path= ./workspace/user/files/test/test.xlsx
        return= test
        """
        return os.path.basename(file_path).split(".")[0]

    @staticmethod
    def uuid_to_file_path(file_uuid: str):
        """根据文件uuid，返回文件路径"""
        wf = GlobalFunction.uuid_to_work_file(file_uuid)
        return wf.file_path if wf else None

    @staticmethod
    def uuid_to_work_file(uuid: str,  # 文件uuid
                          ) -> "WorkFile":
        """根据文件uuid，返回 WorkFile 对象"""
        return GlobalObject._WorkFileMap.get(uuid)

    @staticmethod
    def file_to_work_file(file_path: str,  # 文件路径
                          ) -> "WorkFile":
        """根据文件路径，返回 WorkFile 对象"""
        for wf in GlobalObject._WorkFileMap.values():
            if wf.file_path == file_path:
                return wf
        return None

    @staticmethod
    def uuid_to_file_abstract(uuid: str):
        """根据文件uuid，返回文件摘要"""
        # FIXME mim，通过文件uuid，转换为文件摘要信息dict对象。
        # 假设是这个对象
        return {
            "uuid": uuid,
            "文件路径": os.path.join("./workspace/user/files/test", uuid),
            "文件名称": uuid,
            "hash值": "",
            "更新时间": "",
            "文件预览": "",
            "文件摘要": "",
            "关键实体": "",
        }

    @staticmethod
    def file_to_parse_path(file_path: str):
        """返回文件的解析路径
        解析路径为文件所在目录下的解析目录,解析目录的名称为文件名_解析
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析
        """
        return os.path.join(os.path.dirname(file_path), f"{GlobalFunction.file_to_dir_name(file_path)}_解析")

    @staticmethod
    def file_to_full_text_path(file_path: str):
        """txt文本内容文件路径
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析/test_全文.md
        """
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            f"{GlobalFunction.file_to_name_without_suffix(file_path)}_全文.md"
        )

    @staticmethod
    def file_to_preview_text_path(file_path: str):
        """txt文本内容文件路径预览"""
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            f"{GlobalFunction.file_to_name_without_suffix(file_path)}_预览.md"
        )

    @staticmethod
    def file_to_doc_text_path(file_path: str):
        """文档解析的文本目录
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析/文本
        """
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            "文本"
        )

    @staticmethod
    def file_to_doc_table_path(file_path: str):
        """文档解析的表格目录
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析/表格
        """
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            "表格"
        )

    @staticmethod
    def file_to_doc_image_path(file_path: str):
        """文档解析的图片目录
        path= ./workspace/user/files/test/test.xlsx
        return= ./workspace/user/files/test/test_xlsx_解析/图片
        """
        return os.path.join(
            GlobalFunction.file_to_parse_path(file_path),
            "图片"
        )

    @staticmethod
    def task_id_to_executor_path(task_id: str):
        """任务id转换为任务执行文件路径"""
        return os.path.join(GlobalFunction.task_path(), f"任务_{task_id}/任务执行.md")

    @staticmethod
    def chat_hist_store_file() -> str:
        """生成默认文件路径：chat_年月日.md"""
        date_str = datetime.now().strftime("%Y%m%d")
        return os.path.join(GlobalFunction.chat_path(), f"chat_{date_str}.md")

    @staticmethod
    def parse_llm_think(response: str):
        """解析LLM输出文本中的think内容"""
        """提取<think></think>之间的内容"""
        think_match = re.search(r'<think>(.*?)</think>', response, re.DOTALL)
        if think_match:
            think_text = think_match.group(1) if think_match else ""
            return think_text
        else:
            return None

    @staticmethod
    def parse_llm_reply(response: str):
        """解析LLM输出文本中的reply内容:提取</think>之后的内容。否则返回原文"""
        if "</think>" in response:
            reply_match = re.search(r'(?:.*?</think>)?(.*)', response, re.DOTALL)
            if reply_match:
                reply_text = reply_match.group(1) if reply_match else ""
                return reply_text
            else:
                return response
        else:
            return response

    @staticmethod
    def extract_action_json(response: str):
        """
        解析LLM输出文本中的操作指令
        """
        if "```action" in response:
            predict_action = response.split("```action")[1]
            if predict_action:
                predict_action = predict_action.replace("```action", "```").split("```")[0]
                predict_json = json.loads(predict_action.strip())
                return predict_json
        return None

    @staticmethod
    def extract_data_block(response: str, block_name: str = "action") -> List[dict]:
        """
        提取 response 中的所有最外层代码块。

        - 若块名等于 block_name，则将其内容尝试解析为 JSON 存入返回的"parsed" 列表。
        - 若块名不等于 block_name，则将其内容原样（字符串）构建{"outer_name": content}存入返回 "parsed" 列表。
        - 嵌套在其他块内部的代码块不会被单独提取，而是作为外层块内容的一部分保留。

        参数：
            response (str): 原始文本。
            block_name (str): 需要解析为 JSON 的代码块名称，默认为 "action"。

        返回：
            Dict[str, List[Any]]: 包含两个键：
                - "parsed": list，解析成功的 JSON 对象（如果解析失败，也会以字符串形式存入 raw）。
        """
        lines = response.splitlines(keepends=False)
        stack = []  # 栈元素: (开始行索引, 块名称)
        parsed_results = []  # 存放解析后的 JSON 对象
        start_pattern = re.compile(r'^```\s*(\S+)')  # 匹配开始标记，捕获块名
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 1. 检查是否为开始标记
            if stripped.startswith("```") and len(stripped) > 3:
                match = start_pattern.match(stripped)
                if match:
                    name = match.group(1)
                    # 若当前栈为空，说明这是一个最外层块的开始
                    if not stack:
                        outer_start = i
                        outer_name = name
                    stack.append((i, name))
                i += 1
                continue

            # 2. 检查是否为结束标记
            if stripped == "```":
                if stack:
                    start_idx, name = stack.pop()
                    # 若弹出后栈为空，说明一个最外层块已完整结束
                    if not stack:
                        # 提取块内容（不包含开始和结束行）
                        content_lines = lines[outer_start + 1:i]
                        content = "\n".join(content_lines).strip()
                        if outer_name == block_name:
                            if content.startswith('{') or content.startswith('['):
                                try:
                                    parsed_results.append(json.loads(content))
                                except json.JSONDecodeError as e:
                                    GlobalFunction.log("JSON解析失败", f"错误: {e}\n内容前200字符: {content[:200]}")
                                    parsed_results.append({outer_name: content})
                            else:
                                parsed_results.append({outer_name: content})
                        else:
                            parsed_results.append({outer_name: content})
                i += 1
                continue
            i += 1
        return parsed_results

    @staticmethod
    def parse_llm_output_json(response: str, block_name: str = "action") -> List[dict]:
        think_text = GlobalFunction.parse_llm_think(response)
        # 抽取action行为数据
        json_list = GlobalFunction.extract_data_block(response, block_name)
        if json_list:
            json_list[0]['think'] = think_text
        return json_list

    @staticmethod
    def parse_markdown_title(text: str, block_level: str = "###") -> List[str]:
        """按读取text文本，提取以block_level开头的行后面的标题"""
        lines = text.splitlines(keepends=False)
        title_list = []
        for line in lines:
            if line and line.strip().startswith(block_level + " "):
                title_list.append(line.strip(block_level + " ").strip())
        return title_list

    @staticmethod
    def load_file(file_path: str):
        """Helper function that loads a single file into a string"""
        _, ext = os.path.splitext(file_path)
        with open(file_path, "r", encoding='utf-8') as file:
            if ext == ".json":
                return json.load(file)
            else:
                return file.read().strip()

    @staticmethod
    def write_file(file_path, content: str):
        with open(file_path, "w", encoding='utf-8') as file:
            file.write(content)

    @staticmethod
    def inspect_function_desc(func_cls):
        """检查函数名称，描述，参数信息"""
        parameters = {}
        sig = inspect.signature(func_cls)
        source = inspect.getsource(func_cls)
        doc = inspect.getdoc(func_cls) or ""
        # 去除公共缩进，使代码在顶层合法
        source = textwrap.dedent(source)
        tree = ast.parse(source)
        if tree.body:
            func_def = tree.body[0]
            if isinstance(func_def, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Get all comments from the source code
                comments = {}
                latest_name = None
                for token in tokenize.generate_tokens(io.StringIO(source).readline):
                    if token.type == tokenize.COMMENT and latest_name is not None:
                        line = token.line
                        comment = token.string.strip()
                        comments[latest_name] = comment
                        latest_name = None
                    elif token.type == tokenize.NAME and token.string not in ["int", "str", "list", "dict", "None"]:
                        if token.string:
                            latest_name = token.string

                # Create parameter comment map
                param_comment_map = {}
                for param in func_def.args.args:
                    if param.arg in comments:
                        # Get the comment on the same line as the parameter
                        param_comment_map[param.arg] = comments[param.arg].lstrip('#').strip()

                # 组装参数信息
                for param_name, param in sig.parameters.items():
                    if param_name in ["kwargs", "owner", "state", "self"]:
                        continue
                    p_type = str(param.annotation.__name__) if param.annotation != inspect.Parameter.empty else ""
                    p_comment = param_comment_map.get(param_name, "")
                    if not p_comment and g_yaml_config["dev"]["check_annotation"]:
                        p_comment = f"{func_cls.__name__}，参数 {param_name} 没有写注释"
                        raise ValueError(p_comment)
                    # 组装参数格式
                    parameters[param_name] = f"{p_type},{p_comment}"
        # 组装返回值
        tool = {
            "action": func_cls.__name__,
            "description": doc.strip()
        }
        tool.update(parameters)
        return tool

    @staticmethod
    def log(name: str, msg: Union[str, list[str]]) -> logging.Logger:
        """初始化系统日志记录器"""
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)

        # 避免重复添加handler
        if not logger.handlers:
            # 创建按日期命名的日志文件
            log_file = os.path.join(GlobalFunction.log_path(), f"{name}.log")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            # 创建文件handler
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            # 创建控制台handler
            console_handler = logging.StreamHandler()
            # 设置日志格式
            formatter = logging.Formatter('[%(asctime)s]: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            # 添加handler到logger
            logger.addHandler(file_handler)
            # logger.addHandler(console_handler)
        # Handle both single message and list of messages
        if isinstance(msg, list):
            for message in msg:
                logger.info(message)
        else:
            logger.info(msg)
        return logger

    @staticmethod
    def owner_props_yaml():
        """owner的props文件路径"""
        return os.path.join(GlobalFunction.workspace_path(), "owner_props.yaml")

    @staticmethod
    def embedding_model():
        """获取嵌入模型"""
        return g_yaml_config["model"]["embedding_model"]
