"""pytest 共通フィクスチャ: 一時ディレクトリを ALLOWED_WORK_DIR として各ツールにパッチする。"""
import sys
from pathlib import Path

import pytest

# プロジェクトルートを sys.path に追加（pytest をどこから起動しても動く）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """一時ディレクトリを ALLOWED_WORK_DIR / ALLOWED_WORK_DIRS としてパッチする。"""
    import tools.file_tools as ft
    import tools.command_tools as ct
    import tools.code_tools as cot

    monkeypatch.setattr(ft, "ALLOWED_WORK_DIR", tmp_path)
    monkeypatch.setattr(ft, "ALLOWED_WORK_DIRS", [tmp_path])
    monkeypatch.setattr(ct, "ALLOWED_WORK_DIR", tmp_path)
    monkeypatch.setattr(ct, "ALLOWED_WORK_DIRS", [tmp_path])
    monkeypatch.setattr(cot, "ALLOWED_WORK_DIR", tmp_path)
    return tmp_path
