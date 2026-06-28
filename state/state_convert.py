"""文档转换相关子状态机：pdf→docx、doc→docx、docx→全文、全文→分块"""

import io
import os
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree

from pdf2docx import Converter

from common import GlobalFunction
from .base_state import BaseState, StateOwner
from .state_doc import HeadingChunk


# --------------------------------------------------------------------------
# Pdf2DocxState
# --------------------------------------------------------------------------

class Pdf2DocxState(BaseState):
    """pdf → docx 转换"""
    stateName = "Pdf2DocxState"

    def __init__(self, pdf_path: str, docx_path: str, **kwargs):
        super().__init__(**kwargs)
        self._duration = 10 * 60 * 1000
        self._pdf_path = pdf_path
        self._docx_path = docx_path
        self._thread = None
        self._done = False
        self._error = None

    def Enter(self, owner):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            pdf_path = Path(self._pdf_path)
            docx_path = Path(self._docx_path)
            converter = Converter(str(pdf_path))
            try:
                converter.convert(str(docx_path))
            finally:
                converter.close()
        except BaseException as exc:
            self._error = exc
        finally:
            self._done = True

    def Execute(self, owner):
        if self._error is not None:
            GlobalFunction.log("转换错误", f"pdf → docx 转换失败: 源={self._pdf_path}, 目标={self._docx_path}, 错误={self._error}")
            owner.remove_state(self)
            return
        if not self._done:
            return
        owner.remove_state(self)

    def Exit(self, owner):
        pass


# --------------------------------------------------------------------------
# Doc2DocxState
# --------------------------------------------------------------------------

class Doc2DocxState(BaseState):
    """doc → docx 转换 (依赖 LibreOffice)"""
    stateName = "Doc2DocxState"

    def __init__(self, doc_path: str, docx_path: str, **kwargs):
        '''
        参数：
        doc_path: 源 doc 文件路径
        docx_path: 目标 docx 输出路径
        **kwargs: 其他参数
        '''
        super().__init__(**kwargs)
        self._duration = 10 * 60 * 1000
        self._doc_path = doc_path
        self._docx_path = docx_path
        self._thread: Optional[threading.Thread] = None
        self._done = False
        self._error: Optional[BaseException] = None

    def Enter(self, owner):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            doc_path = Path(self._doc_path)
            docx_path = Path(self._docx_path)
            docx_path.parent.mkdir(parents=True, exist_ok=True)
            soffice = os.getenv("SOFFICE_PATH", "soffice")
            result = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(docx_path.parent),
                    str(doc_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"LibreOffice doc2docx 转换失败: stdout={result.stdout}, stderr={result.stderr}"
                )
            generated_path = docx_path.parent / f"{doc_path.stem}.docx"
            if not generated_path.exists():
                raise RuntimeError(
                    f"LibreOffice 未生成 docx 文件: {generated_path}, stdout={result.stdout}, stderr={result.stderr}"
                )
            if generated_path.resolve() != docx_path.resolve():
                shutil.move(str(generated_path), str(docx_path))
        except BaseException as exc:
            self._error = exc
        finally:
            self._done = True

    def Execute(self, owner):
        if self._error is not None:
            GlobalFunction.log("转换错误", f"doc → docx 转换失败: 源={self._doc_path}, 目标={self._docx_path}, 错误={self._error}")
            owner.remove_state(self)
            return
        if not self._done:
            return
        owner.remove_state(self)

    def Exit(self, owner):
        pass


# --------------------------------------------------------------------------
# Docx2TextState
# --------------------------------------------------------------------------

class Docx2TextState(BaseState):
    """从 docx 提取全文"""
    stateName = "Docx2TextState"

    def __init__(self, docx_path: str, **kwargs):
        '''
        参数：
        docx_path: docx 文件路径
        **kwargs: 其他参数
        '''
        super().__init__(**kwargs)
        self._docx_path = docx_path
        self._done = False

    def Enter(self, owner):
        self._thread = threading.Thread(target=self._run_extract,args=(owner,),daemon=True)
        self._thread.start()

    def _run_extract(self,owner):
        try:
            with zipfile.ZipFile(io.BytesIO(Path(self._docx_path).read_bytes())) as archive:
                xml_content = archive.read("word/document.xml")
            root = ElementTree.fromstring(xml_content)
            namespace = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            }
            texts: List[str] = []
            for paragraph in root.findall(".//w:p", namespace):
                parts = [
                    node.text
                    for node in paragraph.findall(".//w:t", namespace)
                    if node.text
                ]
                if parts:
                    texts.append("".join(parts))
            self._full_text = "\n".join(texts)
        except Exception as e:
            GlobalFunction.log('提取文档文字内容线程出现问题',f"错误为：{e}")
        finally:
            self._done = True        

    def Execute(self, owner):
        if self._done:
            owner.remove_state(self)

    def Exit(self, owner):
        pass


# --------------------------------------------------------------------------
# Text2ChunksState
# --------------------------------------------------------------------------

class Text2ChunksState(BaseState):
    """按标题层级切分文本"""
    stateName = "Text2ChunksState"

    def __init__(self, docx_path: str, full_text: str, **kwargs):
        '''
        参数：
        docx_path: docx 文件路径（用于读取样式信息）
        full_text: 待切分的全文文本
        **kwargs: 其他参数
        '''
        super().__init__(**kwargs)
        self._docx_path = docx_path
        self._full_text = full_text
        self._chunks = []
        self._done = False
        self._duration = 1000*60*10

    def Enter(self, owner: StateOwner):
        self._thread = threading.Thread(target=self._run_extract,args=(owner,),daemon=True)
        self._thread.start()
    
    def _run_extract(self,owner):
        try:
            self.full_text = self._full_text
            actions = owner.execute_prompt('提取文档标题层级',self,None,[])
            tree = actions[0].get('params',{}).get('tree',[])
            if not tree:
                raise ValueError('LLM 返回的标题树为空，未提取到任何章节标题')
            self._chunks = self._json_to_chunks(tree)
            self._fill_content(self._chunks, self.full_text)
            if self._chunks[0].content == '':
                raise ValueError('抽样检测第一个元素的原文发现为空')
        except Exception as e:
            import traceback
            GlobalFunction.log('LLM提取文档章节线程出错',f'错误：{e}\n{traceback.format_exc()}')
        finally:
            self._done = True

    def _json_to_chunks(self, tree):
        result = []
        for node in tree:
            number = node.get("number", "")
            title = node.get("title", "")
            level = int(node.get("level", 1))
            content = node.get("content", "")
            children = node.get("children", [])

            child_chunks = self._json_to_chunks(children)
            chunk = HeadingChunk(
                level=level,
                number=number,
                title=title,
                content=content,
                children=child_chunks,
            )
            result.append(chunk)
        return result

    def _fill_content(self, chunks, full_text):
        """根据 LLM 返回的标题在原文中精确定位，将标题间正文切分填充"""
        ordered = []
        def _dfs(nodes):
            for c in nodes:
                ordered.append(c)
                if c.children:
                    _dfs(c.children)
        _dfs(chunks)
        if not ordered:
            return

        # 第一遍：找每个标题在原文中的位置，存局部 dict
        title_pos = {}  # id(chunk) -> (pos, line_end)，-1 表示未找到
        search_from = 0
        for chunk in ordered:
            pos = full_text.find(chunk.title, search_from)
            # 跳过目录条目（行末带页码，如 "标题  114"）
            while pos >= 0:
                line_end = full_text.find('\n', pos)
                if line_end < 0:
                    line_end = len(full_text)
                after_title = full_text[pos + len(chunk.title):line_end].strip()
                if after_title and after_title.isdigit():
                    pos = full_text.find(chunk.title, line_end)
                else:
                    break
            if pos < 0:
                title_pos[id(chunk)] = (-1, -1)
                continue
            line_end = full_text.find('\n', pos)
            if line_end < 0:
                line_end = len(full_text)
            title_pos[id(chunk)] = (pos, line_end)
            search_from = line_end

        # 第二遍：用标题位置计算正文边界，写入 chunk.content / start_index / end_index
        for i, chunk in enumerate(ordered):
            tpos, tline_end = title_pos[id(chunk)]
            if tpos < 0:
                continue

            title_end = tpos + len(chunk.title)
            same_line_text = full_text[title_end:tline_end].strip()
            if same_line_text:
                content_start = title_end
                while content_start < len(full_text) and full_text[content_start] in (' ', '\t', '　'):
                    content_start += 1
            else:
                content_start = tline_end + 1

            if chunk.children:
                first_child = chunk.children[0]
                first_child_pos = title_pos[id(first_child)][0]
                if first_child_pos > content_start:
                    chunk.content = full_text[content_start:first_child_pos].strip()
                    chunk.start_index = content_start
                    chunk.end_index = first_child_pos
                continue

            content_end = len(full_text)
            for j in range(i + 1, len(ordered)):
                nxt = ordered[j]
                nxt_pos = title_pos[id(nxt)][0]
                if nxt.level <= chunk.level and nxt_pos > 0:
                    content_end = nxt_pos
                    break

            if content_end > content_start:
                chunk.content = full_text[content_start:content_end].strip()
                chunk.start_index = content_start
                chunk.end_index = content_end


    def Execute(self, owner):
        if self._done:
            owner.remove_state(self)

    def Exit(self, owner):
        pass
