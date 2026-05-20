import json
import os.path

import pytest

from app import g_owner, run_server
from state import RefactorTableState, WebSearchState


def xtest_show_refactor_table():
    assert g_owner is not None, "g_owner 不能为空"
    g_owner.add_state(RefactorTableState(source_md_path=os.path.join(g_owner.workspace_path,
                                                                     "user/files/旧目录格式（存放数据）/20260507/docx/丹丹-兴业银行_解析_docx/表/表OCR_图1_0_image1_1.md"),
                                         table_headers=["客户账号", "银行名称", "日期", "交易日期", "摘要", "用途",
                                                        "交易金额", "账户余额", "对方户名"]))



def test_web_search_state():
    """测试 WebSearchState 状态类"""
    assert g_owner is not None, "g_owner 不能为空"
    search_state = WebSearchState(keyword="特朗普访华", num_results=3)
    g_owner.add_state(search_state)

    # 手动执行状态机呼吸一次，让状态执行
    g_owner.breathe(t=1)
    # result = search_state.action_json
    # print(json.dumps(result, ensure_ascii=False, indent=2))
    result = g_owner.web_fetch_result
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    # 启动时自动执行单元测试 执行test_*
    # zhang 不加-cache--clear 不能很好的切换test函数测试
    pytest.main([__file__, "-v", "-s", "--cache-clear"])
    # 测试通过后启动服务器
    run_server()