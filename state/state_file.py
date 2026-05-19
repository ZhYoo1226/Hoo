import hashlib
import os
import uuid as uuid_module
from datetime import datetime
from pathlib import Path

import pandas as pd

from state import BaseState

# 文件摘要索引表列名
# FIXME 这是配置文件决定,读取global或者g_yaml_config
FILES_ABSTRACT_NAME = "files_abstract.parquet"
# FIXME 给一个注释，定义json对象，然后写死摘要的字段，难以阅读
COL_UUID = "uuid"
COL_PATH = "文件路径"
COL_NAME = "文件名称"
COL_HASH = "hash值"
COL_UPDATED = "更新时间"
COL_PREVIEW = "文件预览"
COL_ABSTRACT = "文件摘要"
COL_ENTITIES = "关键实体"
COL_IMPORTANCE = "重要性"
FILES_ABSTRACT_COLUMNS = [
    COL_UUID, COL_PATH, COL_NAME, COL_HASH, COL_UPDATED,
    COL_PREVIEW, COL_ABSTRACT, COL_ENTITIES, COL_IMPORTANCE,
]
SUPPORTED_EXTENSIONS = {".docx"}


class SummaryAbstractState(BaseState):
    """摘要摘要状态"""
    stateName = "SummaryAbstractState"

    def __init__(self, empty_abstract: pd.DataFrame, **kwargs):
        super().__init__(**kwargs)
        self.empty_abstracts = empty_abstract

    def Enter(self, owner):
        pass

    async def Execute(self, owner):
        """总结文件摘要"""
        # 遍历每个摘要为空的文件
        for index, row in self.empty_abstracts.iterrows():
            owner.current_file = row
            # 再次判断是否为空摘要
            if pd.isna(owner.current_file['文件摘要']) or owner.current_file['文件摘要'] == '':
                continue
            action_json = await owner.execute_prompt("总结文件摘要")
            owner.execute_action(action_json)
        self.remove_state(self)

    def Exit(self, owner):
        pass


# FIXME 张耀
class UpdateAbstractState(BaseState):
    """更新摘要索引表 files_abstract.parquet 中的摘要状态"""
    stateName = "UpdateAbstractState"

    def __init__(self, text: str,  # 文件摘要
                 uuid: str,  # 文件uuid
                 **kwargs):
        super().__init__(**kwargs)
        self.abstract_text = text
        self.uuid = uuid

    def Enter(self, owner):
        # 查找并更新内存中的摘要数据
        for idx, row in owner.files_abstract.iterrows():
            if row.get(COL_UUID) == self.uuid:
                owner.files_abstract.at[idx, COL_ABSTRACT] = self.abstract_text
                break

    async def Execute(self, owner):
        """更新文件摘要"""
        # 更新内存对象，同步到 files_abstract.parquet 中
        try:
            files_abstract_path = os.path.join(owner.workspace_path, "user", "files", FILES_ABSTRACT_NAME)
            owner.files_abstract.to_parquet(files_abstract_path, index=False)
            owner.sys_breathe_log(f"文件摘要已更新: {files_abstract_path}")
        except Exception as e:
            owner.sys_breathe_log(f"更新文件摘要失败: {str(e)}")

        owner.remove_state(self)

    def Exit(self, owner):
        pass


# FIXME 巫
class ScanAbstractState(BaseState):
    """扫描文件摘要状态,不存在摘要，则更新摘要"""
    stateName = "ScanAbstractState"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def Enter(self, owner):
        # 重新读取文件摘要
        pass

    def Execute(self, owner):
        # 重新读取文件摘要
        if not self.load_file_abstract(owner):
            owner.remove_state(self)
        else:
            # 将缓存转换为pd.DataFrame类型
            owner.files_abstract = pd(self.file_abstract_cache)

            # 过滤出摘要为空的数据（需要重新扫描的文件）
            # 摘要为空或NaN的文件需要重新处理
            empty_abstract = owner.files_abstract[
                owner.files_abstract['文件摘要'].isna() |
                (owner.files_abstract['文件摘要'] == '')
                ]

            # 如果存在摘要为空的文件，添加扫描状态进行处理
            if not empty_abstract.empty:
                owner.sys_breathe_log(f"发现 {len(empty_abstract)} 个文件缺少摘要，将进行扫描处理")
                owner.add_state(SummaryAbstractState(empty_abstract))

        owner.remove_state(self)
        pass

    def Exit(self, owner):
        pass

    def load_file_abstract(self, owner):
        """读取文件摘要索引表"""
        abstract_path = os.path.join(owner.workspace_path, "user", "files", FILES_ABSTRACT_NAME)
        owner.file_abstract_cache = []

        if not os.path.exists(abstract_path):
            owner.sys_breathe_log(f"文件摘要表不存在: {abstract_path}")
            owner.add_state(ScanFileState())
            return False

        try:
            df = pd.read_parquet(abstract_path)
            df = df.dropna(how='all')

            # 过滤掉空路径行
            if COL_PATH in df.columns:
                df = df[df[COL_PATH].notna() & (df[COL_PATH] != '')]

            self.file_abstract_cache = []
            for _, row in df.iterrows():
                file_info = row.to_dict()
                file_path = str(file_info.get(COL_PATH, ""))
                if not os.path.isabs(file_path):
                    file_info["绝对路径"] = os.path.abspath(os.path.join(owner.workspace_path, file_path))
                else:
                    file_info["绝对路径"] = file_path
                self.file_abstract_cache.append(file_info)

            owner.sys_breathe_log(f"加载文件摘要表完成，共 {len(self.file_abstract_cache)} 条文件记录")

        except Exception as e:
            owner.sys_breathe_log(f"读取文件摘要表失败: {str(e)}")
            return False

        return True

# FIXME 张耀
class ScanFileState(BaseState):
    """
    扫描文件状态：扫描用户workspace/user/files文件目录下的所有docx文件，
    建立文件摘要索引表 files_abstract.parquet
    head: uuid,文件预览,文件摘要,关键实体,重要性,hash值,文件路径,文件名称,更新时间
    """
    stateName = "ScanFileState"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def Enter(self, owner):
        pass

    async def Execute(self, owner):
        files_dir = Path(owner.workspace_path) / "user" / "files"
        if not files_dir.exists():
            return

        abstract_path = files_dir / FILES_ABSTRACT_NAME
        abstract_path.parent.mkdir(parents=True, exist_ok=True)

        if abstract_path.exists() and abstract_path.stat().st_size > 0:
            df = pd.read_parquet(abstract_path)
        else:
            df = pd.DataFrame(columns=FILES_ABSTRACT_COLUMNS)

        for col in FILES_ABSTRACT_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        known_paths = set(df[COL_PATH].tolist()) if not df.empty else set()

        for file_path in files_dir.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.name == FILES_ABSTRACT_NAME:
                continue
            if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            rel_path = self._relative_path(file_path, files_dir)
            file_hash = self._compute_sha256(file_path)

            if rel_path in known_paths:
                existing = df[df[COL_PATH] == rel_path]
                if not existing.empty and existing.iloc[0].get(COL_HASH) == file_hash:
                    continue
            #FIXME 这是读取的文件更新时间？?需要的文件时间
            updated_at = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            record = {
                COL_UUID: str(uuid_module.uuid4()),
                COL_PATH: rel_path,
                COL_NAME: file_path.name,
                COL_HASH: file_hash,
                COL_UPDATED: updated_at,
                COL_PREVIEW: "",
                COL_ABSTRACT: "",
                COL_ENTITIES: "",
                COL_IMPORTANCE: "",
            }

            if rel_path in known_paths:
                idx = df[df[COL_PATH] == rel_path].index[0]
                for key, value in record.items():
                    df.at[idx, key] = value
            else:
                df.loc[len(df)] = record
                known_paths.add(rel_path)

        df = df[FILES_ABSTRACT_COLUMNS]
        df.to_parquet(abstract_path, index=False)

    @staticmethod
    def _relative_path(file_path: Path, files_dir: Path) -> str:
        return str(file_path.relative_to(files_dir)).replace("\\", "/")


    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def Exit(self, owner):
        pass
