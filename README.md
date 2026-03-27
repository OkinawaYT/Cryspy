# Cryspy

CrySPY 実行補助と CIF 生成スクリプトです。

## uv 環境構築

通常:

```bash
uv sync
```

CHGNet 連携 (`calc_in/ase_in_CHGNet.py`) を使う場合:

```bash
uv sync --extra chgnet
```

## 実行例

```bash
uv run python main.py
uv run python run_cryspy.py
uv run python cryspy_make_cifs.py
```

## 依存ライブラリ

- 標準: `pymatgen`
- オプション: `chgnet`

## 備考

CrySPY 本体コマンドの実行環境は別途必要です。
