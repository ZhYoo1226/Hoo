"""
WebSearchState & WebFetchState - 网络搜索与内容获取状态

WebSearchState: 基于 Searx 搜索引擎的网络搜索，获取 URL 列表
WebFetchState: 使用 Crawl4AI 批量爬取 URL 内容
"""
import ast
import asyncio
import json
from typing import List, Dict, Any

from crawl4ai import AsyncWebCrawler

from langchain_community.agent_toolkits.load_tools import load_tools

from .base_state import BaseState


class WebSearchState(BaseState):
    """
    联网搜索状态
    通过关键词执行网络搜索，完成后自动创建 WebFetchState 抓取 URL 内容。
    """
    stateName = "WebSearchState"

    def __init__(self,
                 keyword: str,  # 搜索关键词
                 searx_host: str = "http://10.143.135.91:8083",  # Searx 服务地址
                 num_results: int = 5,  # 返回结果数量
                 **kwargs):
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
            "action": "state:WebSearchState",
            "thought": f"准备搜索关键词: {self.keyword}",
            "data": {"keyword": self.keyword, "count": 0}
        }

    def Execute(self, owner):
        """执行搜索，完成后创建 WebFetchState"""
        try:
            results = self._execute_search()

            # 提取 URL 列表
            urls = [r.get("link") or r.get("url") for r in results if r.get("link") or r.get("url")]

            # 主动创建 WebFetchState
            owner.add_state(WebFetchState(urls=urls, search_results=results))

            self.action_json = {
                "action": "state:WebSearchState",
                "thought": f"搜索关键词 [{self.keyword}] 完成，发现 {len(urls)} 个 URL",
                "data": {"keyword": self.keyword, "results": results, "count": len(results)}
            }
        except Exception as e:
            self.action_json = {
                "action": "state:WebSearchState",
                "thought": f"搜索出错: {str(e)}",
                "data": {"keyword": self.keyword, "results": [], "count": 0, "error": str(e)}
            }
        finally:
            owner.remove_state(self)

    def Exit(self, owner):
        """退出状态"""
        pass


class WebFetchState(BaseState):
    """
    获取URL数据状态
    使用 Crawl4AI 批量爬取 URL 内容，完成后存储到 owner.web_fetch_result。
    """
    stateName = "WebFetchState"

    def __init__(self,
                 urls: List[str],  # URL 列表
                 search_results: List[Dict[str, Any]] = None,  # 原始搜索结果
                 **kwargs):
        super().__init__(**kwargs)
        self.urls = urls
        self.search_results = search_results or []

    def Enter(self, owner):
        """进入状态时的初始化"""
        self.action_json = {
            "action": "state:WebFetchState",
            "thought": f"准备抓取 {len(self.urls)} 个 URL",
            "data": {"count": 0, "total": len(self.urls)}
        }

    def Execute(self, owner):
        """批量爬取 URL 内容（同步方法，内部使用 asyncio）"""
        datas = []
        try:
            datas = asyncio.run(self._fetch_all())
        except Exception as e:
            # 如果 asyncio.run 抛出异常（可能是嵌套事件循环问题），尝试同步爬取
            datas = self._fetch_sync()

        # 存储到 owner
        owner.web_fetch_result = {"datas": datas, "count": len(datas), "total": len(self.urls)}

        # 构建结果摘要
        summary = self._build_summary(datas)
        owner.record_role_chat("助手", summary)

        success_count = sum(1 for d in datas if not d.get("error"))
        self.action_json = {
            "action": "state:WebFetchState",
            "thought": f"抓取完成，成功 {success_count}/{len(datas)} 个页面",
            "data": {"datas": datas, "count": len(datas), "total": len(self.urls)}
        }
        owner.remove_state(self)

    async def _fetch_all(self):
        """异步批量爬取"""
        datas = []
        async with AsyncWebCrawler() as crawler:
            for url in self.urls:
                try:
                    result = await crawler.arun(url=url)
                    metadata = result.metadata or {}
                    datas.append({
                        "url": url,
                        "markdown": result.markdown,
                        "title": metadata.get("title"),
                        "error": None
                    })
                except Exception as e:
                    datas.append({
                        "url": url,
                        "markdown": None,
                        "title": None,
                        "error": str(e)
                    })
        return datas

    def _fetch_sync(self):
        """同步回退方案：使用同步爬取"""
        datas = []
        for url in self.urls:
            try:
                result = asyncio.run(AsyncWebCrawler().arun(url=url))
                metadata = result.metadata or {}
                datas.append({
                    "url": url,
                    "markdown": result.markdown,
                    "title": metadata.get("title"),
                    "error": None
                })
            except Exception as e:
                datas.append({
                    "url": url,
                    "markdown": None,
                    "title": None,
                    "error": str(e)
                })
        return datas

    def _build_summary(self, datas: List[Dict[str, Any]]) -> str:
        """构建抓取结果摘要"""
        lines = [f"已获取 {len(datas)} 个页面的内容:\n"]
        for i, item in enumerate(datas, 1):
            if item.get("error"):
                lines.append(f"{i}. [{item['url']}] - 抓取失败: {item['error']}")
            else:
                title = item.get("title") or "无标题"
                lines.append(f"{i}. **{title}**\n   URL: {item['url']}\n   内容长度: {len(item.get('markdown') or '')} 字符")
        return "\n".join(lines)

    def Exit(self, owner):
        """退出状态"""
        pass
