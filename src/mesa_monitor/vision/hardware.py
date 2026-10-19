"""Detección de hardware: SO, CPU, RAM y GPU (NVIDIA/CUDA o Apple MPS).

Funciona aunque torch no esté instalado (ej. en la imagen de la API): en ese caso usa
`nvidia-smi` para saber si hay una placa NVIDIA.
"""

import contextlib
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil


@dataclass
class GpuInfo:
    name: str
    vram_gb: float | None
    backend: str  # "cuda" | "mps"
    cuda_version: str | None = None
    compute_capability: str | None = None
    driver: str | None = None


@dataclass
class HardwareInfo:
    os: str
    os_release: str
    machine: str
    in_docker: bool
    in_wsl: bool
    cpu_name: str
    cpu_physical_cores: int | None
    cpu_logical_cores: int | None
    ram_gb: float
    torch_version: str | None
    gpus: list[GpuInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def best_device(self) -> str:
        if any(g.backend == "cuda" for g in self.gpus):
            return "cuda"
        if any(g.backend == "mps" for g in self.gpus):
            return "mps"
        return "cpu"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["best_device"] = self.best_device
        return data


def _cpu_name() -> str:
    system = platform.system()
    if system == "Linux":
        with contextlib.suppress(OSError):
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    if system == "Darwin" and shutil.which("sysctl"):
        with contextlib.suppress(subprocess.SubprocessError, OSError):
            out = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            return out.stdout.strip()
    return platform.processor() or platform.machine()


def _in_wsl() -> bool:
    with contextlib.suppress(OSError):
        return "microsoft" in Path("/proc/version").read_text().lower()
    return False


def _nvidia_smi() -> list[GpuInfo]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            [  # noqa: S607
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        name, mem, driver = (p.strip() for p in line.split(","))
        gpus.append(
            GpuInfo(name=name, vram_gb=round(float(mem) / 1024, 1), backend="cuda", driver=driver)
        )
    return gpus


def detect_hardware() -> HardwareInfo:
    info = HardwareInfo(
        os=platform.system(),
        os_release=platform.release(),
        machine=platform.machine(),
        in_docker=Path("/.dockerenv").exists(),
        in_wsl=_in_wsl(),
        cpu_name=_cpu_name(),
        cpu_physical_cores=psutil.cpu_count(logical=False),
        cpu_logical_cores=psutil.cpu_count(logical=True),
        ram_gb=round(psutil.virtual_memory().total / 1024**3, 1),
        torch_version=None,
    )
    try:
        import torch
    except ImportError:
        torch = None  # type: ignore[assignment]

    smi = _nvidia_smi()
    if torch is not None:
        info.torch_version = str(torch.__version__)
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                driver = smi[i].driver if i < len(smi) else None
                info.gpus.append(
                    GpuInfo(
                        name=props.name,
                        vram_gb=round(props.total_memory / 1024**3, 1),
                        backend="cuda",
                        cuda_version=torch.version.cuda,
                        compute_capability=f"{props.major}.{props.minor}",
                        driver=driver,
                    )
                )
        elif torch.backends.mps.is_available():
            info.gpus.append(GpuInfo(name="Apple GPU (MPS)", vram_gb=None, backend="mps"))
        if smi and not torch.cuda.is_available():
            info.notes.append(
                "Hay una placa NVIDIA pero torch no puede usarla: instalá la variante CUDA "
                "(perfil gpu de Docker, o `uv sync --extra vision --extra cuda`)."
            )
    elif smi:
        info.gpus.extend(smi)
        info.notes.append("torch no está instalado: la GPU se detectó solo con nvidia-smi.")
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        info.notes.append("CUDA_VISIBLE_DEVICES está vacío: la GPU está oculta para este proceso.")
    return info
