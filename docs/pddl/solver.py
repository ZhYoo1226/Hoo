import json
import re

import yaml
from z3 import *


def check_behavior_model(model, input_var_yaml):
    """
    检查行为模型，基于输入 YAML 配置，输出缺失的变量（实体）并更新 YAML 文件。

    Args:
        model (str or dict): 行为模型的 JSON 字符串或已解析的字典。
        input_var_yaml (str): 输入 YAML 文件路径，包含已提供的实体值。

    Returns:
        list: 缺失的实体名称列表。
    """
    # 1. 解析行为模型
    if isinstance(model, str):
        data = json.loads(model)
    else:
        data = model

    # 2. 获取模型中的步骤列表
    models = data.get("models", [])
    # 3. 从步骤中提取所有被使用的实体（谓词参数）
    used_entities = set()
    # 正则匹配谓词调用：谓词名(参数, 参数, ...)
    pattern = r'\b\w+\s*\(([^)]*)\)'
    for step in models:
        match = re.search(pattern, step)
        if match:
            args_str = match.group(1)
            # 分割参数，去除空白和引号
            # 参数可能是带引号的字符串，也可能是不带引号的标识符
            # 简单按逗号分割，然后去掉空白和可能的引号
            args = [arg.strip().strip("'\"") for arg in args_str.split(',') if arg.strip()]
            used_entities.update(args)
        else:
            # 如果没有匹配，尝试按原始字符串处理（比如"连接服务器(90服务器IP, SSH端口, SSH账号, SSH密码)"）
            # 简单处理：如果包含括号则提取括号内内容
            if '(' in step and ')' in step:
                inner = step.split('(')[1].split(')')[0]
                args = [arg.strip().strip("'\"") for arg in inner.split(',') if arg.strip()]
                used_entities.update(args)

    # 4. 读取输入 YAML 配置（如果文件不存在则视为空字典）
    try:
        with open(input_var_yaml, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"警告：配置文件 {input_var_yaml} 不存在，视为空配置。")
        config = {}

    # 5. 已知实体（值不为 "不知道"）
    known_entities = {k: v for k, v in config.items() if v != "不知道"}
    known_set = set(known_entities.keys())

    # 6. 缺失实体 = 使用的实体中不在已知集合中的
    missing = [e for e in used_entities if e not in known_set]

    # 7. 更新 YAML 文件：保留已知实体，缺失的实体赋值为 "不知道"
    updated_config = known_entities.copy()
    for entity in missing:
        updated_config[entity] = "不知道"
    with open(input_var_yaml, 'w', encoding='utf-8') as f:
        yaml.dump(updated_config, f, allow_unicode=True, default_flow_style=False)

    # 8. 使用 Z3 验证逻辑一致性（可选）
    # 为每个实体创建布尔变量表示是否已知
    known_vars = {e: Bool(f"known_{e}") for e in used_entities}
    solver = Solver()

    # 添加已知实体的约束
    for e in known_set:
        if e in known_vars:
            solver.add(known_vars[e] == True)

    # 为每个步骤创建可执行性约束
    step_executables = []
    for idx, step in enumerate(models):
        # 提取该步骤的参数实体
        match = re.search(r'\b\w+\s*\(([^)]*)\)', step)
        if match:
            args_str = match.group(1)
            args = [arg.strip().strip("'\"") for arg in args_str.split(',') if arg.strip()]
        else:
            # 降级处理
            if '(' in step and ')' in step:
                inner = step.split('(')[1].split(')')[0]
                args = [arg.strip().strip("'\"") for arg in inner.split(',') if arg.strip()]
            else:
                args = []

        if args:
            # 步骤可执行当且仅当所有参数实体已知
            step_cond = And([known_vars[arg] for arg in args if arg in known_vars])
            step_var = Bool(f"step_{idx}")
            solver.add(step_var == step_cond)
            step_executables.append(step_var)
        else:
            # 无参数步骤，默认可执行
            step_var = Bool(f"step_{idx}")
            solver.add(step_var == True)
            step_executables.append(step_var)

    # 添加目标：所有步骤都应可执行（即部署成功）
    if step_executables:
        solver.add(And(step_executables))

    # 求解
    if solver.check() == sat:
        # 可满足，不需要额外动作
        pass
    else:
        # 不可满足，说明已知条件与步骤执行要求矛盾
        print("警告：已知条件不足以使所有步骤执行，请检查配置或模型。")

    return missing


# 使用示例
if __name__ == "__main__":
    with open('task.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(data)
    missing_vars = check_behavior_model(data, "input_var.yaml")
    print("缺失的变量:", missing_vars)
