import json
import os.path
from pathlib import Path

import pytest

from app import g_owner, run_server
from common import GlobalFunction, g_yaml_config, ChatMessage
from state import MetaReadState, ExecutePromptState, RefactorTableState, ClearChatState, ClearTaskState, \
    ClearEvalAdviceState, EvalState, LiveState, WebSearchState
from state.state_doc import WordParseState
from state.state_ocr import ImgOCRState
from state.state_task import ScanTaskState, ExecuteTaskState
from state.state_xlsx import ParseExcelState


# FIXME 张耀: 测试文件解析
def utest_file_parse():
    file_path = os.path.join(g_owner.workspace_path, "user/files/test/test.xlsx")
    g_owner.add_state(MetaReadState(file_path, "稍等，我阅读一下文件", "用户突然传来文件，没有明显的需求和任务归属。"))


def u1test_xlsx_state():
    assert g_owner is not None, "g_owner 不能为空"
    g_owner.add_state(ParseExcelState(xlsx_path=os.path.join(g_owner.workspace_path, "user/files/test/test.xlsx")))


def u1test_show_file_structure():
    log_text = GlobalFunction.show_file_structure()
    print(log_text)


def xtest_prompt_execute_preview():
    g_owner.add_state(ExecutePromptState("修正数据表格式"))


def xtest_show_refactor_table():
    assert g_owner is not None, "g_owner 不能为空"
    g_owner.add_state(RefactorTableState(source_md_path=os.path.join(g_owner.workspace_path,
                                                                     "user/files/旧目录格式（存放数据）/20260507/docx/丹丹-兴业银行_解析_docx/表/表OCR_图1_0_image1_1.md"),
                                         table_headers=["客户账号", "银行名称", "日期", "交易日期", "摘要", "用途",
                                                        "交易金额", "账户余额", "对方户名"]))


# 测试前 检查是否有旧产物（重跑wordparsestate，效果更清晰）
def ztest_img_ocr_state():
    root = Path(g_owner.workspace_path) / "user/files/202605/罗玉华-中国银行__docx__解析/图"
    image_paths = sorted(
        p for p in root.rglob("*")
        if p.suffix.lower() in ImgOCRState.MEDIA_CONTENT_TYPES
    )
    imgocr_state = ImgOCRState(image_paths=image_paths, batch_index=0, auto_install=False)
    g_owner.add_state(imgocr_state)


# zhang 提前删除产物，看效果（有幂等检查）
def utest_word_parse_state():
    root = Path(g_owner.workspace_path) / "user/files/202605"
    for pdf_path in root.rglob("*.pdf"):
        g_owner.add_state(WordParseState(str(pdf_path)))


# zhang 仅实现ScanTaskState
def ztest_scan_task():
    """扫描现有的任务"""
    g_owner.add_state(ScanTaskState())
    g_owner.add_state(ExecuteTaskState())


def untest_eval_state():
    """评估状态"""
    if g_yaml_config["state"]["launch_eval"]:
        # 评估提示词
        g_owner.add_state(EvalState())


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

def wtest_web_search_state():
    """测试 WebSearchState 状态类"""
    assert g_owner is not None, "g_owner 不能为空"
    search_state = WebSearchState(keyword="AI Agent", num_results=3)
    g_owner.add_state(search_state)

    # 手动执行状态机呼吸一次，让状态执行
    g_owner.breathe(t=1)

    result = search_state.action_json
    print(json.dumps(result, ensure_ascii=False, indent=2))

def test_wait_input_state():
    """等待输入状态"""
    clear_local_chat_data_state()
    g_owner.add_state(LiveState())

    g_owner.recv_message("用户", "你叫什么名字？")
    g_owner.recv_message("用户", "今天是星期几？")
    g_owner.recv_message("用户", "今年是农历的哪年？")
    g_owner.recv_message("用户", "今天什么节气？")
    g_owner.recv_message("用户", "你可否帮我设计一个提示词，推断总结文件的内容摘要.")
    g_owner.recv_message("用户", "你知道我是谁吗？")


if __name__ == "__main__":
    # 启动时自动执行单元测试 执行test_*
    # zhang 不加-cache--clear 不能很好的切换test函数测试
    pytest.main([__file__, "-v", "-s", "--cache-clear"])
    # 测试通过后启动服务器
    run_server()
