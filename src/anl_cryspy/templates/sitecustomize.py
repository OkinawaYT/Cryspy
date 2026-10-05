# CrySPY 本体プロセスの乱数を固定するための注入(PYTHONPATH 経由、cryspy 起動時のみ有効)。
# seed = 基本seed と work/fin の完了数から決める(世代生成は全ジョブ完了後に起こるので決定的)。
import os
import sys

if os.environ.get("ANL_CRYSPY_SEED") and os.path.basename(sys.argv[0]) == "cryspy":
    import random
    _n = len(os.listdir("work/fin")) if os.path.isdir("work/fin") else 0
    _s = int(os.environ["ANL_CRYSPY_SEED"]) * 1000003 + _n
    random.seed(_s)
    try:
        import numpy
        numpy.random.seed(_s % (2**32))
    except Exception:
        pass

    # pyxtal.from_random は random_state 未指定だと未シードの RNG を使うため、seed 済みの
    # random から random_state を補う(これが無いと同一 seed でも構造が変わる)。
    try:
        import functools
        import pyxtal as _px

        _orig = _px.pyxtal.from_random

        @functools.wraps(_orig)
        def _from_random(self, *a, **kw):
            if kw.get("random_state") is None:
                kw["random_state"] = random.randrange(2**31 - 1)
            return _orig(self, *a, **kw)

        _px.pyxtal.from_random = _from_random
    except Exception:
        pass
