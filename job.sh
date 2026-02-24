#!/bin/bash
#PBS -q i2cpu
#PBS -N cryspy
#PBS -l select=1:ncpus=126:mpiprocs=1:ompthreads=1
#PBS -l walltime=00:30:00
#PBS -m e -M y.tatetsu@meio-u.ac.jp

USER_BIN_DIR="/home/k0298/k029800/bin"
CRYSPY_BIN_DIR="~/.local/bin/"

if [ -n "$PBS_O_WORKDIR" ]; then
  cd "$PBS_O_WORKDIR"
fi

echo "Job started on $(hostname) at $(date)"
echo "Workdir: $(pwd)"

# 1. 環境の初期化とロード
module purge
source ~/.bashrc

export PATH="${USER_BIN_DIR}:${CRYSPY_BIN_DIR}:${PATH}"

# CPU数を環境変数に設定 (PBS_NCPUSが利用可能な場合)
if [ -n "$PBS_NCPUS" ]; then
  export CRYSPY_NUM_WORKERS=$PBS_NCPUS
  echo "Setting CRYSPY_NUM_WORKERS to $CRYSPY_NUM_WORKERS"
else
  export CRYSPY_NUM_WORKERS=4
  echo "PBS_NCPUS not set, using default: 4"
fi

echo "Checking cryspy path:"
which chgnet_opt
ls -l $(which chgnet_opt)

echo "=== Job Start ==="
touch ready

echo "Starting run_cryspy.py..."
python "${USER_BIN_DIR}/run_cryspy.py"

echo "Job Finished at $(date)."

