# test_cryspy workflow

このリポジトリは CrySPY で構造探索を行い、その結果から CIF を生成する 2 段階フローです。

## 必要環境
- CrySPY コマンドが実行できる環境
- Python 3 環境
- `pymatgen` (CIF 生成用。`generate_all_cifs.py`を使うときのみ必須)

## ファイル概要
- `run_batch.py` : 並列で CrySPY を回し、結果を `result_natot*` に退避
- `generate_all_cifs.py` : 全計算完了後、各結果フォルダの `data/init_POSCARS` から CIF を生成し、フォルダ内 `input/` に保存
- `makeCIF.py` : 単発で `data/init_POSCARS` を CIF 化するスクリプト（バッチ処理では使用しない）

## 使い方
1. **構造探索を実行**
   ```bash
   python run_batch.py
   ```
   - 設定は `run_batch.py` 冒頭の `NATOT_MIN/MAX` や `ELEMENTS` を編集してください。
   - 各ケースの結果は `result_natot{N}_FeX_SiY_AlZ/` に `data/`, `cryspy.stat`, `err_cryspy`, `log_cryspy`, `lock_cryspy` として保存されます。

2. **CIF を一括生成（全計算完了後）**
   ```bash
   python generate_all_cifs.py
   ```
   - 事前に `pymatgen` をインストールしてください。
   - 各 `result_natot*` フォルダに `input/` が作成され、`*_ID_* .cif` が出力されます。

## よくある確認事項
- `generate_all_cifs.py` 実行時に `ModuleNotFoundError: pymatgen` が出た場合は、使用中の Python 環境に `pip install pymatgen` してください。
- 既存の結果フォルダにも後から `generate_all_cifs.py` を実行すれば CIF を生成できます。
