"""スケジューラ別の job.sh 生成。ヘッダ(#PBS / #SBATCH)だけがスケジューラ依存で、本体は共通。

クラスタとスケジューラは Command/cluster/config/clusters.yml に合わせる(systemB=slurm/i8cpu、systemC=pbs/i2cpu)。
Command のテンプレート流儀(_hdr_pbs.j2 / _hdr_slurm.j2 のヘッダ部品 + 共通本体)に倣うが、Command には依存しない。
`anl-cryspy jobscript --cluster systemB -o job.sh`。リポジトリ直下の job_pbs.sh / job_slurm.sh / job.sh(=pbs)は生成物。
"""
from __future__ import annotations

from pathlib import Path

MAIL = "y.tatetsu@meio-u.ac.jp"
CLUSTERS = {"systemB": "slurm", "systemC": "pbs"}
QUEUES = {"slurm": "i8cpu", "pbs": "i2cpu"}  # i キュー(30 分・対話枠)
BODY = Path(__file__).parent / "templates" / "job_body.sh"


def scheduler_for(cluster: str | None = None, scheduler: str | None = None) -> str:
    if scheduler:
        if scheduler not in QUEUES:
            raise ValueError(f"unknown scheduler {scheduler!r} (pbs|slurm)")
        return scheduler
    if cluster not in CLUSTERS:
        raise ValueError(f"unknown cluster {cluster!r} ({'|'.join(CLUSTERS)}); give --scheduler pbs|slurm")
    return CLUSTERS[cluster]


def header(scheduler: str, queue: str | None = None, walltime: str = "00:30:00", mail: str = MAIL,
           ncpus: int = 126, name: str = "cryspy") -> str:
    q = queue or QUEUES[scheduler]
    if scheduler == "pbs":
        return (f"#!/bin/bash\n#PBS -q {q}\n#PBS -N {name}\n"
                f"#PBS -l select=1:ncpus={ncpus}:mpiprocs=1:ompthreads=1\n"
                f"#PBS -l walltime={walltime}\n#PBS -m e -M {mail}\n")
    return (f"#!/bin/bash\n#SBATCH -p {q}\n#SBATCH -N 1\n#SBATCH -n 1\n#SBATCH -c {ncpus}\n"
            f"#SBATCH --time={walltime}\n#SBATCH --job-name={name}\n"
            f"#SBATCH --mail-type=FAIL --mail-user={mail}\n")


def render(scheduler: str, **kw) -> str:
    if scheduler == "slurm":
        kw.setdefault("ncpus", 16)  # i8cpu は 1 ジョブ 16 cpu 枠(clusters.yml max_cpus)
    return header(scheduler, **kw) + "\n" + BODY.read_text()
