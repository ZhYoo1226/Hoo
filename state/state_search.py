"""
WebSearchState - 网络搜索状态

提供基于 Searx 搜索引擎的网络搜索功能，返回结构化的 action_json 数据。
"""
import ast
import json
from typing import List, Dict, Any

from langchain_community.agent_toolkits.load_tools import load_tools

from .base_state import BaseState


class WebSearchState(BaseState):
    """
    网络搜索状态
    
    通过关键词执行网络搜索，返回包含 action、thought、data 字段的 action_json 数据结构。
    """
    stateName = "WebSearchState"

    def __init__(self, keyword: str, searx_host: str = "http://10.143.135.91:8083",
                 num_results: int = 5, **kwargs):
        """
        初始化 WebSearchState
        
        Args:
            keyword: 搜索关键词
            searx_host: Searx 服务地址，默认使用内网地址
            num_results: 返回结果数量，默认 5
        """
        super().__init__(**kwargs)
        self.keyword = keyword
        self.searx_host = searx_host
        self.num_results = num_results
        self._search_tool = None

    def _init_search_tool(self):
        """初始化搜索工具"""
        if self._search_tool is None:
            tools = load_tools(
                ["searx-search-results-json"],
                searx_host=self.searx_host,
                num_results=self.num_results
            )
            self._search_tool = tools[0]

    def _execute_search(self) -> List[Dict[str, Any]]:
        """
        执行搜索并返回结果列表
        
        Returns:
            搜索结果列表，每个结果包含 title、link、content 字段
        """
        self._init_search_tool()

        raw_results = self._search_tool.run(self.keyword)

        results = []
        if raw_results:
            try:
                parsed = ast.literal_eval(raw_results) if isinstance(raw_results, str) else raw_results
                if isinstance(parsed, list):
                    for item in parsed:
                        result = {
                            "title": item.get("title", ""),
                            "link": item.get("link", item.get("url", "")),
                            "content": item.get("snippet", item.get("content", ""))
                        }
                        results.append(result)
                else:
                    results = [{"title": "", "link": "", "content": str(parsed)}]
            except (json.JSONDecodeError, ValueError):
                results = [{"title": "", "link": "", "content": str(raw_results)}]

        return results

    def Enter(self, owner):
        """进入状态时的初始化"""
        self.action_json = {
            "action": "state:GetURLDataState",
            "thought": f"准备搜索关键词: {self.keyword}",
            "data": {
                "keyword": self.keyword,
                "results": [],
                "count": 0
            }
        }

    def Execute(self, owner):
        """执行搜索并构建 action_json"""
        try:
            results = self._execute_search()
            self.action_json = {
                "action": "state:GetURLDataState",
                "thought": f"正在搜索关键词: {self.keyword}",
                "data": {
                    "keyword": self.keyword,
                    "results": results,
                    "count": len(results)
                }
            }
        except Exception as e:
            self.action_json = {
                "action": "state:GetURLDataState",
                "thought": f"搜索出错: {str(e)}",
                "data": {
                    "keyword": self.keyword,
                    "results": [],
                    "count": 0,
                    "error": str(e)
                }
            }
        finally:
            # 执行完成后从状态机中移除自己，与其他状态保持一致
            owner.remove_state(self)

    def Exit(self, owner):
        """退出状态"""
        pass


class GetURLDataState(BaseState):
    """
    获取URL数据状态

    通过 URL 获取数据，返回包含 action、thought、data 字段的 action_json 数据结构。
    """
    stateName = "GetURLDataState"
