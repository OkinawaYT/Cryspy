import os

# テスト中の generate は PyPI 照会・自動更新を行わない(更新ロジック自体は test_versioning.py でモック検証)。
os.environ.setdefault("ANL_CRYSPY_AUTO_UPDATE", "0")
os.environ.setdefault("ANL_CRYSPY_NO_REEXEC", "1")

import tempfile

os.environ.setdefault("ANL_CRYSPY_STATE_DIR", tempfile.mkdtemp(prefix="anl_cryspy_state_"))
