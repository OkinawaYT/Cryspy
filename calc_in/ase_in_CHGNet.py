import warnings
from chgnet.model import CHGNet
from chgnet.model.dynamics import CHGNetCalculator

# 警告を抑制（ログが見やすくなるように）
warnings.filterwarnings("ignore")

# --- CHGNetの設定 ---

# モデルのロード
# デフォルトの事前学習済みモデルを使用します。
# 毎回ロードすると重いため、大規模探索ではGPUメモリに注意が必要です。
# use_device='cuda' とすることでGPUを使用します（CPUなら 'cpu'）
model = CHGNet.load()

# Calculatorの作成
# stress_weight: 構造最適化でセルも緩和させる場合に重要です
calc = CHGNetCalculator(
    model=model,
    use_device='cuda', # GPUがない場合は 'cpu' に変更してください
    stress_weight=0.1
)

# --- CrySPYとの連携 ---

# CrySPYは実行時にこのスクリプトを読み込み、
# そのコンテキスト内に 'atoms' という変数名で構造データを用意しています。
# ユーザーが行うべきは、その atoms に calculator をセットすることだけです。

atoms.calc = calc

# 必要であれば、ここで初期磁気モーメントなどを設定することも可能です
# atoms.set_initial_magnetic_moments([...])