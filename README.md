# Cryspy

## これは何か / なぜ存在するか

このディレクトリは、結晶構造予測ツール **CrySPY**（進化的アルゴリズム/ランダムサーチによる結晶構造探索。PyPI パッケージ `cryspy`、CLI コマンド `cryspy`）を、複数の組成（元素の種類・原子数）にわたって自動的にバッチ実行するための補助スクリプト集です。CrySPY 本体のソースコードはここには含まれておらず、別途インストールされた `cryspy` コマンド（本環境では `/opt/homebrew/bin/cryspy` としてグローバルインストール済み）を `subprocess` 経由で呼び出す「ラッパー／ドライバ」です。

やっていることは大きく3段階です。

1. 探索したい組成の範囲（例: 全原子数 3〜20、元素 Fe/Si/Al）を `input.json`（Python版バッチランナー）または `input.dat`（シェル版バッチランナー）で指定する。
2. 組成の全組み合わせを列挙し、組み合わせごとに一時ディレクトリで `cryspy.in` の `natot`/`nat`/`atype`（および `mindist_*`）を書き換えながら `cryspy` コマンドを繰り返し実行する。
3. 各実行結果（`data/`, `cryspy.stat`, `log_cryspy` 等）を組成名のディレクトリ（例: `Fe1Si1Al2/`）に集約し、必要なら `data/init_POSCARS` から CIF ファイルを生成する（`cryspy_make_cifs.py`、pymatgen 使用）。

DFT リポジトリ内での位置づけ: `DFT/STATUS.md` および `DFT/Command/STATUS.md`（両ファイルは将来削除予定）では、`Cryspy` は `MaterialsProject`, `Matlantis` などと並ぶ「解析」系パッケージの一つとして、ディレクトリ命名規約上 `0_Cryspy/` への統一が計画されている程度の言及に留まります。他の DFT/* や Command/* パッケージのコードが `Cryspy` の関数やモジュールを import している箇所はなく（grep で確認済み）、実質的に独立した単体ツールです。将来的にクラスター（Command リポジトリ側）へのデプロイも検討されていますが、現状は未着手です。

`src/main.py` / `src/anl_cryspy/` は `pyproject.toml` の `anl-cryspy` エントリポイント用の雛形で、現状は `Hello from cryspy!` を表示するだけのプレースホルダです（実質未使用）。

## セットアップ

Python 3.13 以上が必要です（`pyproject.toml` の `requires-python`）。依存関係管理には [uv](https://github.com/astral-sh/uv) を使います。

```bash
cd /Users/tatetsu/GitHub/DFT/Cryspy
uv sync
```

CHGNet（機械学習ポテンシャル）を計算エンジンとして使う場合（`calc_in/ase_in_CHGNet.py`）:

```bash
uv sync --extra chgnet
```

依存ライブラリ:
- 必須: `pymatgen>=2025.10.7`
- オプション（`chgnet` extra）: `chgnet>=0.4.0`

**重要:** CrySPY 本体（`cryspy` コマンド）はこの `pyproject.toml` の依存に含まれていません。別途インストールしておく必要があります（本環境では `which cryspy` で `/opt/homebrew/bin/cryspy` が見つかる状態）。CrySPY 未インストールの環境では `run_cryspy.py` や `runCryspy.sh` は `cryspy: command not found` 相当のエラーで失敗します。

## ファイルマップ / 設定リファレンス

### バッチ実行スクリプト（2系統ある。用途に応じてどちらかを使う）

- `src/run_cryspy.py` — Python 製バッチランナー。`input.json` を読み、`ProcessPoolExecutor` で複数組成を並列実行する。CLI引数 `-n`/`--num-workers` または環境変数 `CRYSPY_NUM_WORKERS` でワーカー数を上書き可能（優先順位: CLI引数 > 環境変数 > `input.json` の `num_workers` > デフォルト4）。各組成を `tempfile.TemporaryDirectory()` に `cryspy.in` / `job_cryspy` / `ase_in.py` をコピーして実行し、終わったら `<元素><数>...`（例 `Fe1Si1Al2`）というディレクトリに結果を退避する。全ケース終了後、`makecif: true` なら `cryspy_make_cifs.py` を呼んで CIF を生成する。
- `runCryspy.sh` — 同等の処理をシェルスクリプトで行う旧実装。`input.dat` を `source` して設定を読み込み、組成の組み合わせを bash の再帰関数で列挙し、`cryspy.in` を `sed` で書き換えながらカレントディレクトリで直接 `cryspy` を実行する（並列化なし、逐次実行）。実行後 `makeCIF.py` を呼ぶ想定になっているが、そのスクリプトはこのリポジトリには存在しない（`cryspy_make_cifs.py` とは別名で未整備）。

どちらか一方を使う運用と思われ、両者は独立して動作します（同時実行は想定されていません）。

### 設定ファイル

- `input.json` — `run_cryspy.py` 用設定。キー: `elements`（探索する元素リスト）, `natot_min`/`natot_max`（全原子数の探索範囲）, `cryspy_in`（雛形とする `.in` ファイル名）, `num_workers`（並列数）, `makecif`（CIF自動生成の有無）。
- `input.dat` — `runCryspy.sh` 用設定（bash構文でそのまま `source` される）。`tot_max`（探索する全原子数の上限）, `min`/`max`（元素ごとの原子数レンジ配列）, `atype`（元素配列）, `mindist_1`/`mindist_2`/`mindist_3`（元素間の最小原子間距離、`atype` の並び順に対応）。
- `cryspy.in` — CrySPY 本体が読む設定ファイルの雛形。`run_cryspy.py`/`runCryspy.sh` はこのファイルの `natot`/`atype`/`nat`（と `mindist_*`）を組成ごとに書き換えて使う。主要セクション:
  - `[basic]`: `algo`（探索アルゴリズム。`EA`=進化的アルゴリズム, `RS`=ランダムサーチ）, `calc_code = ASE`（構造最適化に ASE を使う）, `tot_struc`（総試行構造数）, `nstage`, `njob`, `jobcmd`, `jobfile`（後述の `job_cryspy` を指す）。
  - `[EA]`（`algo = EA` の場合のみ）: 世代あたりの個体数 `n_pop` と、交叉 `n_crsov` / 入替 `n_perm` / ひずみ `n_strain` / ランダム `n_rand` の内訳（合計が `n_pop` に一致する必要あり、ファイル内コメントに明記）、エリート数 `n_elite`、選択方式 `slct_func`、トーナメントサイズ `t_size`、最大世代数 `maxgen_ea` など。
  - `[structure]`: `natot`（全原子数）, `atype`（元素種）, `nat`（各元素の原子数）, `mindist_1/2/3`（最小原子間距離の行列、`atype` の順序に対応）。
  - `[ASE]`: `ase_python`（ASE計算スクリプトのファイル名。`calc_in/` 配下または実行時にコピーされる `ase_in.py` を指す）。
  - `[option]`: 空（未使用）。
- `cryspy_.in`, `cryspy_EA.in`, `cryspy_PdCo.in`, `cryspy_RS.in` — 過去の実行例・テンプレートのバリエーション（Pd-Cu-Co系、Pd-Co系、EA版、RS版など）。現在アクティブに参照されているのは `cryspy.in` のみで、これらは参考用のスナップショットと見られる。

### 計算エンジン設定（`calc_in/`）

- `calc_in/ase_in.py_1` — ASE を使った構造最適化スクリプトのサンプル。EMT（経験的ポテンシャル）で `POSCAR` を読み込み `BFGS` で最適化し、CrySPY が要求する規約（`log.tote` にエネルギー、`CONTCAR` に構造）で出力する。ファイル名の末尾 `_1` のため、`cryspy.in` の `ase_python = ase_in.py` として使うには `ase_in.py` にリネームする必要がある（`run_cryspy.py` は `ase_in.py`/`ase_in.py_1` のどちらかを自動で探してコピーする)。
- `calc_in/ase_in_CHGNet.py` — CHGNet（機械学習ポテンシャル）を計算エンジンとして使うバージョン。`atoms.calc` に `CHGNetCalculator` をセットするだけの内容で、`use_device='cuda'` がデフォルト（GPUが無い環境では `'cpu'` に変更が必要、とコメントに明記）。
- `calc_in/job_cryspy` — ASE計算を起動するジョブスクリプト。`python3 ase_in.py` を実行し、CrySPY が監視する `stat_job` の3行目を `done` に書き換える（CrySPYのジョブ管理プロトコル準拠）。

### ジョブ投入（PBS）

- `job.sh` — PBS（`#PBS -q i2cpu` 等）ジョブスクリプト。`~/.local/bin/run_cryspy.py`（本リポジトリの `src/run_cryspy.py` をユーザーの bin にデプロイしたもの想定）を実行し、`CRYSPY_NUM_WORKERS` を `PBS_NCPUS` から自動設定する。宛先メールアドレスがハードコードされている（`y.tatetsu@meio-u.ac.jp`）。

### src/ レイアウト

- `src/main.py` — `anl-cryspy` エントリポイントの実体。現状プレースホルダ（`print("Hello from cryspy!")` のみ）。
- `src/run_cryspy.py` — 上述のバッチランナー本体。
- `src/cryspy_make_cifs.py` — 各結果ディレクトリの `data/init_POSCARS` を pymatgen でパースし、`<結果ディレクトリ>/input/` に CIF ファイル群を書き出す後処理スクリプト。
- `src/anl_cryspy/cli.py`, `src/anl_cryspy/__init__.py` — `anl-cryspy` CLI のラッパー。`cli.py` は `src/main.py` を `runpy` 経由で実行するだけ。`__init__.py` は空。
- `src/cryspy.egg-info/` — `uv sync`/`pip install -e` 実行時に生成されるビルドメタデータ（手動編集不要）。

## 使い方（具体例）

### Python版（推奨・並列実行可）

```bash
cd /Users/tatetsu/GitHub/DFT/Cryspy
uv sync
# input.json を編集して探索したい元素・原子数範囲を指定
uv run python src/run_cryspy.py            # input.json の num_workers を使用
uv run python src/run_cryspy.py -n 8       # ワーカー数を明示指定
CRYSPY_NUM_WORKERS=16 uv run python src/run_cryspy.py
```

実行後、`Fe1Si1Al2/` のような組成名ディレクトリに CrySPY の出力（`data/`, `cryspy.stat` など）が格納され、`makecif: true`（デフォルト）であれば CIF も自動生成される。CIF だけ後から作り直したい場合:

```bash
uv run python src/cryspy_make_cifs.py
```

### シェル版（逐次実行、旧実装）

```bash
cd /Users/tatetsu/GitHub/DFT/Cryspy
# input.dat を編集（tot_max, min, max, atype, mindist_1/2/3）
./runCryspy.sh
```

このスクリプトはカレントディレクトリの `cryspy.in` を直接書き換えながら実行するため、実行中に他のジョブと同じディレクトリを共有しないこと。`makeCIF.py` を呼ぶ行があるが、そのファイルは本リポジトリに存在しないため、CIF生成が必要なら `src/cryspy_make_cifs.py` を手動で実行するか、`runCryspy.sh` 内の該当行を差し替える。

### PBS クラスターでの実行

```bash
qsub job.sh
```

`job.sh` は `~/.local/bin/run_cryspy.py` を呼ぶため、事前に `src/run_cryspy.py`（および `cryspy_make_cifs.py`、`input.json`、`cryspy.in`、`calc_in/` 一式）をユーザーの `~/.local/bin/` 相当にデプロイしておく必要がある。

## 既知の問題・注意点

- **CrySPY本体は同梱されていない。** `pyproject.toml` の依存関係に `cryspy` パッケージが含まれておらず、`cryspy` コマンドが `PATH` に無い環境では即座に失敗する。
- **`ase_in.py_1` はリネームが必要。** `cryspy.in` の `[ASE] ase_python = ase_in.py` はファイル名 `ase_in.py` を期待しているが、リポジトリ内には `ase_in.py_1`（末尾に `_1`）としてしか存在しない。`run_cryspy.py` は自動でこれを見つけてコピーするが、`runCryspy.sh` 経由や手動実行の場合はリネームまたはコピーが必要。
- **`runCryspy.sh` は `makeCIF.py` を呼ぶが、そのファイルは存在しない。** CIF生成をシェル版で自動化したい場合は別途対応が必要（`src/cryspy_make_cifs.py` を呼ぶよう修正するのが妥当）。
- **`job.sh` のメール通知先がハードコード** されている（`y.tatetsu@meio-u.ac.jp`）。別ユーザーが使う場合は書き換えが必要。
- **`cryspy_.in`, `cryspy_EA.in`, `cryspy_PdCo.in`, `cryspy_RS.in` は未使用の過去スナップショット。** どのスクリプトからも参照されておらず、実験条件を変えた際の記録として残っている可能性が高い。整理する場合は内容を確認の上で削除・アーカイブを検討。
- **`src/main.py`/`anl_cryspy` の `anl-cryspy` CLI は実質プレースホルダ。** 現状 "Hello from cryspy!" を出すだけで、`run_cryspy.py` や `cryspy_make_cifs.py` を呼ぶ統合CLIにはなっていない。
- **CHGNet 連携時の `use_device='cuda'` がデフォルト。** GPUの無い環境（多くのログインノード等）では `calc_in/ase_in_CHGNet.py` 内のコメント通り `'cpu'` に変更しないとエラーになる。
- **他パッケージからの依存は無い。** grep で確認した限り、`DFT/`・`Command/` 配下の他コードは `Cryspy` のモジュールを import しておらず、ディレクトリ命名規約上の言及（将来 `0_Cryspy/` へ統一予定）に留まる。この README の更新・削除が他パッケージの動作に影響することはない。

## 推奨される次のステップ

- `cryspy` を `pyproject.toml` の依存に追加する（PyPI公開版があれば）か、少なくともセットアップ手順に「別途 `pip install cryspy` 等が必要」と明記する運用を徹底する。
- `ase_in.py_1` を `ase_in.py` にリネームするか、`cryspy.in` 側の参照名を `ase_in.py_1` に合わせて明示的に統一する。
- `runCryspy.sh` が参照する `makeCIF.py` を `src/cryspy_make_cifs.py` の呼び出しに置き換えるか、シェル版自体を Python 版（`run_cryspy.py`）に一本化して二重メンテナンスを解消する。
- `src/main.py`/`anl_cryspy/cli.py`（`anl-cryspy` コマンド）を、実際に `run_cryspy.py` の機能を呼び出す実用的なエントリポイントとして実装するか、不要であれば `pyproject.toml` の `[project.scripts]` ごと削除する。
- `cryspy_.in`, `cryspy_EA.in`, `cryspy_PdCo.in`, `cryspy_RS.in` の要否を確認し、不要なら削除、必要なら「どの実験に対応する設定か」をファイル名かコメントで明確化する。
- `job.sh` のメール通知先など環境固有のハードコード値を、環境変数か設定ファイルに切り出す。
- DFT リポジトリ全体でディレクトリ命名規約の統一（`0_Cryspy/` への移行）が検討されている場合、このディレクトリ名の変更が `job.sh` の `~/.local/bin/run_cryspy.py` 呼び出しパスやクラスターデプロイ設定に影響しないか、移行時に確認する。
