import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

import pandas as pd

from .base_state import BaseState
from .state_file import COL_HASH, COL_PATH, FILES_ABSTRACT_NAME


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class DocxImage:
    # zhang: docx 中抽取的一张图片。

    index: int
    rel_id: str
    media_name: str
    extension: str
    position: int
    blob: bytes


@dataclass
class DocxTable:
    # zhang: docx 中抽取的一个表格，保留结构化行列和 markdown 文本。

    index: int
    position: int
    title: str
    rows: List[List[str]]
    markdown: str


@dataclass
class HeadingChunk:
    # zhang: 文本切分后的标题节点，children 表示下级标题。

    level: int
    number: str
    title: str
    content: str
    children: List["HeadingChunk"] = field(default_factory=list)


# --------------------------------------------------------------------------
# 模块级常量
# --------------------------------------------------------------------------

PARSE_DIR_SUFFIX = "__docx__解析"

HEADING_PATTERN = re.compile(r"^Heading\s*(\d+)$", re.IGNORECASE)
TOC_STYLE_PATTERN = re.compile(r"^toc\s*(\d+)$", re.IGNORECASE)
# FIXME 张 不完善
CHINESE_HEADING_PATTERN = re.compile(
    r"^(第?[一二三四五六七八九十百千万零〇]+)[、章节部分篇]\s*(.+)$"
)
# FIXME 张 topc style 123?->456?
NUMERIC_HEADING_PATTERN = re.compile(r"^(\d+(?:\.\d+)+)(?:\.\s+|\s+|、|．)(.+)$")
LIST_ITEM_PATTERN = re.compile(
    r"^(?:\(?\d+\)|（\d+）|\([一二三四五六七八九十]+\)|\d+、)"
)

REL_NAMESPACE = {
    "r": "http://schemas.openxmlformats.org/package/2006/relationships"
}

_CONF_DIR = Path(__file__).resolve().parent.parent / "workspace" / "conf"
MEDIA_CONTENT_TYPES: dict = json.loads(
    (_CONF_DIR / "imagetype.json").read_text(encoding="utf-8")
)


# --------------------------------------------------------------------------
# 模块级工具函数
# --------------------------------------------------------------------------

def _sanitize_name(name: str) -> str:
    # 文件名安全化：移除非法字符，截断至 60 字符
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name.strip())
    safe = safe.strip(". ")
    if len(safe) > 60:
        safe = safe[:60].rstrip(". ")
    return safe or "未命名"


# --------------------------------------------------------------------------
# 子状态机 (从拆分文件导入)
# --------------------------------------------------------------------------

from .state_convert import Doc2DocxState, Docx2TextState, Pdf2DocxState, Text2ChunksState  # noqa: E402
from .state_extract import ExtractDocxImagesState, ExtractDocxTablesState  # noqa: E402


# --------------------------------------------------------------------------
# 混合编排状态机
# --------------------------------------------------------------------------

class WordParseState(BaseState):
    # zhang: 文档解析混合状态机。输入 source_path，按阶段依次添加子状态机执行解析管线，最终输出写入磁盘文件。

    stateName = "WordParseState"

    # 阶段常量
    STAGE_INIT = "init"
    STAGE_NORMALIZE = "normalize"
    STAGE_TABLES = "tables"
    STAGE_IMAGES = "images"
    STAGE_TEXT = "text"
    STAGE_CHUNKS = "chunks"

    def __init__(self, source_path: str, output_root: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.source_path = source_path
        self.output_root = output_root
        self._duration = 60 * 60 * 1000
        self._stage = self.STAGE_INIT
        self._current_sub_state: Optional[BaseState] = None
        self._done = False
        self._started = False

        # 中间结果
        self._source_hash = ""
        self._docx_path = ""
        self._parsed_root: Optional[Path] = None
        self._image_dir: Optional[Path] = None
        self._table_dir: Optional[Path] = None
        self._text_dir: Optional[Path] = None
        self._full_text_path: Optional[Path] = None
        self._document_dir: Optional[Path] = None
        self._normalized_docx_path: Optional[Path] = None
        self._tables: List[DocxTable] = []
        self._images: List[DocxImage] = []
        self._full_text = ""
        self._chunks: List[HeadingChunk] = []

    def Enter(self, owner):
        # 初始化路径、计算 hash、创建目录，幂等检查通过则跳过
        if self._started:
            return
        self._started = True

        source = Path(self.source_path).expanduser().resolve()
        if not source.exists():
            owner.log("解析错误", f"源文件不存在: {source}")
            self._done = True
            return

        # 计算路径
        if self.output_root:
            self._document_dir = Path(self.output_root).expanduser().resolve()
        else:
            self._document_dir = source.parent
        self._normalized_docx_path = self._document_dir / f"{source.stem}.docx"
        self._parsed_root = self._document_dir / f"{source.stem}{PARSE_DIR_SUFFIX}"
        self._image_dir = self._parsed_root / "图"
        self._table_dir = self._parsed_root / "表"
        self._text_dir = self._parsed_root / "文字"
        self._full_text_path = self._parsed_root / f"{source.stem}_全文.txt"
        self._source_hash = self._file_sha256(source)

        # 创建目录
        for d in [self._document_dir, self._parsed_root, self._image_dir, self._table_dir, self._text_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 幂等检查
        if self._check_idempotent(owner):
            self._done = True
            owner.event.put("WordParseState_done")
            return

    def Execute(self, owner):
        # 等待子状态完成 → 推进到下一阶段
        if self._done:
            owner.remove_state(self)
            return

        # 等待子状态完成
        if (
            self._current_sub_state is not None 
            and
            self._current_sub_state in owner._states
            ):
            return

        if self._stage == self.STAGE_INIT:
            self._advance_from_init(owner)
        elif self._stage == self.STAGE_NORMALIZE:
            self._advance_from_normalize(owner)
        elif self._stage == self.STAGE_TABLES:
            self._advance_from_tables(owner)
        elif self._stage == self.STAGE_IMAGES:
            self._advance_from_images(owner)
        elif self._stage == self.STAGE_TEXT:
            self._advance_from_text(owner)
        elif self._stage == self.STAGE_CHUNKS:
            self._advance_from_chunks(owner)

    def Exit(self, owner):
        pass

    # --------------------------------------------------------------------------
    # 阶段推进
    # --------------------------------------------------------------------------

    def _advance_from_init(self, owner):
        # 根据源文件类型分发：docx 直接提取表格，pdf/doc 先转为 docx
        source_ext = Path(self.source_path).suffix.lower()
        docx_path = str(self._normalized_docx_path)

        if source_ext == ".docx":
            source = Path(self.source_path).expanduser().resolve()
            if source.resolve() != self._normalized_docx_path.resolve():
                shutil.copy2(source, self._normalized_docx_path)
            self._docx_path = docx_path
            self._add_sub_state(owner, ExtractDocxTablesState(docx_path))
            self._stage = self.STAGE_TABLES
        elif source_ext == ".pdf":
            self._add_sub_state(owner, Pdf2DocxState(self.source_path, docx_path))
            self._stage = self.STAGE_NORMALIZE
        elif source_ext == ".doc":
            self._add_sub_state(owner, Doc2DocxState(self.source_path, docx_path))
            self._stage = self.STAGE_NORMALIZE
        else:
            owner.log("解析错误", f"暂不支持的文件类型: {source_ext}")
            self._done = True

    def _advance_from_normalize(self, owner):
        # pdf/doc 已转为 docx，检查转换结果，推进到表格提取
        sub_state = self._current_sub_state
        if sub_state._error is not None:
            owner.log("解析错误", f"pdf/doc → docx 转换失败: {sub_state._error}")
            self._done = True
            return
        self._docx_path = str(self._normalized_docx_path)
        self._add_sub_state(owner, ExtractDocxTablesState(self._docx_path))
        self._stage = self.STAGE_TABLES

    def _advance_from_tables(self, owner):
        # 表格提取完成，推进到图片提取
        self._tables = self._current_sub_state._tables
        self._add_sub_state(owner, ExtractDocxImagesState(self._docx_path))
        self._stage = self.STAGE_IMAGES

    def _advance_from_images(self, owner):
        # 图片提取完成，推进到全文文本提取
        self._images = self._current_sub_state._images
        self._add_sub_state(owner, Docx2TextState(self._docx_path))
        self._stage = self.STAGE_TEXT

    def _advance_from_text(self, owner):
        # 全文提取完成，推进到标题分块
        self._full_text = self._current_sub_state._full_text
        self._add_sub_state(owner, Text2ChunksState(self._docx_path, self._full_text))
        self._stage = self.STAGE_CHUNKS

    def _advance_from_chunks(self, owner):
        # 分块完成，写入全部输出产物，标记完成
        self._chunks = self._current_sub_state._chunks
        self._write_outputs(owner)
        if not hasattr(owner, "parsed_document_cache"):
            owner.parsed_document_cache = {}
        owner.parsed_document_cache[self._source_hash] = str(self._parsed_root)
        self._done = True
        owner.event.put("WordParseState_done")

    # --------------------------------------------------------------------------
    # 辅助
    # --------------------------------------------------------------------------

    def _add_sub_state(self, owner, state):
        # 记录当前子状态引用，加入 owner 状态列表
        self._current_sub_state = state
        owner.add_state(state)

    def _check_idempotent(self, owner) -> bool:
        # 1. 内存缓存：同会话已解析过，直接返回
        if not hasattr(owner, "parsed_document_cache"):
            owner.parsed_document_cache = {}
        if owner.parsed_document_cache.get(self._source_hash) == str(self._parsed_root):
            return True

        # 2. 文件摘要表：hash 匹配说明内容未变更
        abstract_path = Path(owner.workspace_path) / "user" / "files" / FILES_ABSTRACT_NAME
        hash_matched = False
        if abstract_path.exists() and abstract_path.stat().st_size > 0:
            df = pd.read_parquet(abstract_path)
            if COL_PATH in df.columns:
                files_dir = Path(owner.workspace_path) / "user" / "files"
                try:
                    rel_path = str(Path(self.source_path).relative_to(files_dir)).replace("\\", "/")
                except ValueError:
                    rel_path = Path(self.source_path).name
                matched = df[df[COL_PATH] == rel_path]
                if not matched.empty and matched.iloc[0].get(COL_HASH) == self._source_hash:
                    hash_matched = True
        if not hash_matched:
            return False

        # 3. 磁盘产物完整性：防止手误删除
        if not self._parsed_root.exists():
            return False
        if not self._normalized_docx_path.exists():
            return False
        if not self._full_text_path.exists():
            return False
        if self._text_dir.exists():
            for path in self._text_dir.rglob("内容.txt"):
                if any(child.is_dir() for child in path.parent.iterdir()):
                    return False

        owner.parsed_document_cache[self._source_hash] = str(self._parsed_root)
        return True

    # --------------------------------------------------------------------------
    # 输出与工具方法
    # --------------------------------------------------------------------------

    @staticmethod
    def _file_sha256(path: Path) -> str:
        # 计算文件 SHA256，用于幂等判断和变更检测
        digest = hashlib.sha256()
        with path.open("rb") as file:
            while True:
                chunk = file.read(8192)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _relative_manifest_path(manifest_path: Path, target_path: Path) -> str:
        # 计算相对路径，优先相对于 workspace 根目录
        workspace_root = next(
            (parent for parent in manifest_path.parents if parent.name == "workspace"),
            None,
        )
        if workspace_root is not None:
            try:
                return str(target_path.relative_to(workspace_root)).replace("\\", "/")
            except ValueError:
                pass
        return os.path.relpath(target_path, manifest_path.parent).replace("\\", "/")

    @staticmethod
    def _join_outline_lines(lines: Sequence[str]) -> str:
        # 空行过滤 + 末尾补换行，供目录文件写出
        clean_lines = [line for line in lines if line]
        return ("\n".join(clean_lines) + "\n") if clean_lines else ""

    @staticmethod
    def _write_image_blob(image: DocxImage, image_path: Path) -> None:
        # 图片 blob 直写入磁盘，旋转校正由 PaddleOCR 内置方向分类器处理
        image_path.write_bytes(image.blob)

    def _chunk_file_stem(self, chunk: HeadingChunk) -> str:
        # 标题节点 → 安全的目录名（编号.标题_sha1前8位）
        safe_title = _sanitize_name(chunk.title)
        if chunk.level == 0 and chunk.title == "前言":
            digest = hashlib.sha1(chunk.title.encode("utf-8")).hexdigest()[:8]
            return f"前言_{digest}"
        raw_name = f"{chunk.number}.{chunk.title}"
        digest = hashlib.sha1(raw_name.encode("utf-8")).hexdigest()[:8]
        safe_number = _sanitize_name(chunk.number)
        return f"{safe_number}.{safe_title}_{digest}"

    def _write_chunk_tree(self, chunk: HeadingChunk, parent_dir: Path) -> None:
        # 递归将标题节点树写入磁盘目录
        chunk_dir = parent_dir / self._chunk_file_stem(chunk)
        chunk_dir.mkdir(parents=True, exist_ok=True)

        if not chunk.children:
            content_path = chunk_dir / "内容.txt"
            body = chunk.content.strip()
            content_path.write_text(body + ("\n" if body else ""), encoding="utf-8")

        for child in chunk.children:
            self._write_chunk_tree(child, chunk_dir)

    def _append_heading_children(
        self, chunk: HeadingChunk, level2_lines: List[str], level3_lines: List[str]
    ) -> None:
        # 提取节点的二级子标题，追加到目录行列表
        level2_children = [child for child in chunk.children if child.level == 2]
        if level2_children:
            level2_lines.append(chunk.title)
            for child in level2_children:
                level2_lines.append(f"  - {child.title}")
                self._append_heading_grandchildren(child, level3_lines, chunk.title)

    def _append_heading_grandchildren(
        self,
        chunk: HeadingChunk,
        level3_lines: List[str],
        level1_title: str = "",
    ) -> None:
        # 提取节点的三级子标题，追加到目录行列表
        level3_children = [child for child in chunk.children if child.level == 3]
        if not level3_children:
            return
        header = f"{level1_title} / {chunk.title}" if level1_title else chunk.title
        level3_lines.append(header)
        for child in level3_children:
            level3_lines.append(f"  - {child.title}")

    def _write_heading_outline_files(self, parsed_root: Path, chunks: Sequence[HeadingChunk]) -> None:
        # 从标题树提取一/二/三级目录，写入三个 txt 文件
        level1_lines: List[str] = []
        level2_lines: List[str] = []
        level3_lines: List[str] = []

        for chunk in chunks:
            if chunk.level == 1:
                level1_lines.append(chunk.title)
                self._append_heading_children(chunk, level2_lines, level3_lines)
            elif chunk.level == 2:
                level2_lines.append(chunk.title)
                self._append_heading_grandchildren(chunk, level3_lines)
            elif chunk.level == 3:
                level3_lines.append(chunk.title)

        (parsed_root / "一级目录信息.txt").write_text(
            self._join_outline_lines(level1_lines), encoding="utf-8"
        )
        (parsed_root / "二级目录信息.txt").write_text(
            self._join_outline_lines(level2_lines), encoding="utf-8"
        )
        (parsed_root / "三级目录信息.txt").write_text(
            self._join_outline_lines(level3_lines), encoding="utf-8"
        )

    def _write_outputs(self, owner):
        # 将所有中间结果写入磁盘：表格md、图片+元数据、全文txt、目录文件、标题树、manifest
        for d in [self._document_dir, self._parsed_root, self._image_dir, self._table_dir, self._text_dir]:
            d.mkdir(parents=True, exist_ok=True)

        for table in self._tables:
            stem = f"表{table.index}_{table.position}_{table.title}"
            markdown_path = self._table_dir / f"{stem}.md"
            markdown_path.write_text(table.markdown, encoding="utf-8")

        for image in self._images:
            stem = f"图{image.index}"
            image_path = self._image_dir / f"{stem}{image.extension}"
            self._write_image_blob(image, image_path)
            try:
                rel_img = str(image_path.relative_to(Path(owner.workspace_path))).replace("\\", "/")
            except ValueError:
                rel_img = image_path.name
            result = {
                "docx_rel_id": image.rel_id,
                "docx_media_name": image.media_name,
                "图片路径": rel_img,
                "图片类型": "",
                "内容概述": "",
                "文字识别": "",
            }
            output_md = self._image_dir / f"{stem}_图片解析.md"
            output_md.write_text(
                f"# {stem} 图片解析\n\n"
                f"## 图片信息摘要\n\n"
                f"```json\n"
                f"{json.dumps(result, ensure_ascii=False, indent=2)}\n"
                f"```\n\n"
                f"## 图片处理\n",
                encoding="utf-8",
            )

        self._full_text_path.write_text(self._full_text, encoding="utf-8")
        self._write_heading_outline_files(self._parsed_root, self._chunks)

        for chunk in self._chunks:
            self._write_chunk_tree(chunk, self._text_dir)

        manifest_source = self._relative_manifest_path(self._parsed_root / "parse_manifest.md", Path(self.source_path))
        manifest_docx = self._relative_manifest_path(self._parsed_root / "parse_manifest.md", self._normalized_docx_path)
        (self._parsed_root / "parse_manifest.md").write_text(
            "\n".join([
                f"source_hash: {self._source_hash}",
                f"source_path: {manifest_source}",
                f"normalized_docx: {manifest_docx}",
            ]) + "\n",
            encoding="utf-8",
        )


__all__ = ["WordParseState",]
