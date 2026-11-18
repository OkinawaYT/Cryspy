#!/bin/bash
#PBS -q i2cpu
#PBS -N cryspy
#PBS -l select=1:ncpus=126:mpiprocs=1:ompthreads=1
#PBS -l walltime=00:30:00
#PBS -m e -M y.tatetsu@meio-u.ac.jp

# CrySPY は専用環境 ~/DFT/Cryspy/.venv に一本化(2026-11-18)。CRYSPY_DIR / CRYSPY_BIN で上書き可。
# 戻し方: CRYSPY_BIN=$HOME/.local/bin/cryspy(旧来の pip --user 版)、RUN_CRYSPY=$HOME/bin/run_cryspy.py
CRYSPY_DIR="${CRYSPY_DIR:-$HOME/DFT/Cryspy}"
export CRYSPY_BIN="${CRYSPY_BIN:-${CRYSPY_DIR}/.venv/bin/cryspy}"
RUN_CRYSPY="${RUN_CRYSPY:-${CRYSPY_DIR}/src/run_cryspy.py}"
# 計算ノードでは自動更新しない(更新はログインノードで: cd ~/DFT/Cryspy && .venv/bin/anl-cryspy update)
export ANL_CRYSPY_AUTO_UPDATE=0

if [ -n "$PBS_O_WORKDIR" ]; then
  cd "$PBS_O_WORKDIR"
fi

echo "Job started on $(hostname) at $(date)"
echo "Workdir: $(pwd)"

# 1. 環境の初期化とロード
module purge
source ~/.bashrc

export PATH="$HOME/bin:${PATH}"

# CPU数を環境変数に設定 (PBS_NCPUSが利用可能な場合)
if [ -n "$PBS_NCPUS" ]; then
  export CRYSPY_NUM_WORKERS=$PBS_NCPUS
  echo "Setting CRYSPY_NUM_WORKERS to $CRYSPY_NUM_WORKERS"
else
  export CRYSPY_NUM_WORKERS=4
  echo "PBS_NCPUS not set, using default: 4"
fi

echo "CRYSPY_BIN=${CRYSPY_BIN} ($("${CRYSPY_BIN%/cryspy}/python" -c 'import importlib.metadata as m; print(m.version("csp-cryspy"))' 2>/dev/null || echo '?'))"
echo "Checking chgnet_opt path:"
which chgnet_opt
ls -l $(which chgnet_opt)

echo "=== Job Start ==="
touch ready

echo "Starting run_cryspy.py..."
python "${RUN_CRYSPY}"

echo "Job Finished at $(date)."

