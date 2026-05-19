import os.path
from pathlib import Path

import pytest

from app import g_owner, run_server
from common import g_yaml_config
# 行续符，等价于括号下的换行写法 python主动处理括号、方括号、花括号的换行
from state import MetaReadState, ClearChatState, ClearTaskState, \
    ClearEvalAdviceState
from state.state_doc import WordParseState
from state.state_ocr import TableOCRState
from state.state_task import ScanTaskState, ExecuteTaskState


# FIXME 张耀: 测试文件解析
def utest_file_parse():
    file_path = os.path.join(g_owner.workspace_path, "user/files/test/test.xlsx")
    g_owner.add_state(MetaReadState(file_path, "稍等，我阅读一下文件", "用户突然传来文件，没有明显的需求和任务归属。"))

# 测试前 检查是否有旧产物（ 重跑 word parse state，效果更清晰 ）
def ztest_img_ocr_state():
    root = Path(g_owner.workspace_path) / "user/files/202605/罗玉华-中国银行__docx__解析/图"
    imgocr_state = TableOCRState(source_path=str(root), batch_index=0, auto_install=False)
    g_owner.add_state(imgocr_state)

# zhang 提前删除产物，看效果（有幂等检查）
def test_word_parse_state():
    root = Path(g_owner.workspace_path) / "user/files/202605"
    for filePath in root.rglob("*.pdf"):
        parse_state = WordParseState(str(filePath))
        g_owner.add_state(parse_state)


# zhang 仅实现ScanTaskState
def ztest_scan_task():
    """扫描现有的任务"""
    g_owner.add_state(ScanTaskState())
    g_owner.add_state(ExecuteTaskState())


def clear_local_chat_data_state():
    """清除所有状态"""
    # 清除聊天记录
    if g_yaml_config["chat"]["launch_clear_chat"]:
        g_owner.add_state(ClearChatState())
    # 清除任务
    if g_yaml_config["chat"]["launch_clear_task"]:
        g_owner.add_state(ClearTaskState())
    # 清除评估建议
    if g_yaml_config["chat"]["launch_clear_prompt_eval"]:
        g_owner.add_state(ClearEvalAdviceState())


if __name__ == "__main__":
    # 启动时自动执行单元测试 执行test_*
    # zhang 不加-cache--clear 不能很好的切换test函数测试
    pytest.main([__file__, "-v", "-s", "--cache-clear"])
    # 测试通过后启动服务器  先注释服务器启动
    run_server()
