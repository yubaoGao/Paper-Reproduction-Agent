"""Read-only NVIDIA inventory discovery through the vendor CLI."""

from __future__ import annotations

import csv
import io
import subprocess
from datetime import datetime, timezone

from backend.app.domain import GPUDevice, GPUDeviceState


class NVIDIAInventoryError(RuntimeError):
    pass


class NvidiaSMIInventoryProvider:
    """Production adapter; callers may skip it when NVIDIA tooling is absent."""

    def __init__(self, *, executable: str = "nvidia-smi", timeout_seconds: int = 10) -> None:
        if timeout_seconds < 1:
            raise ValueError("NVIDIA inventory timeout must be positive")
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def discover(self) -> tuple[GPUDevice, ...]:
        command = (
            self.executable,
            "--query-gpu=index,memory.total,memory.free,name",
            "--format=csv,noheader,nounits",
        )
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NVIDIAInventoryError("NVIDIA inventory discovery failed") from exc
        if result.returncode != 0:
            raise NVIDIAInventoryError("nvidia-smi returned a non-zero status")
        observed = datetime.now(timezone.utc)
        devices = []
        try:
            for row in csv.reader(io.StringIO(result.stdout), skipinitialspace=True):
                if not row:
                    continue
                gpu_id, total, available, model_name = (item.strip() for item in row)
                devices.append(
                    GPUDevice(
                        gpu_id=gpu_id,
                        total_memory_mb=int(total),
                        available_memory_mb=int(available),
                        state=GPUDeviceState.AVAILABLE,
                        model_name=model_name,
                        evidence=("nvidia-smi query-gpu inventory",),
                        observed_at=observed,
                    )
                )
        except (TypeError, ValueError) as exc:
            raise NVIDIAInventoryError("nvidia-smi returned an invalid inventory row") from exc
        return tuple(sorted(devices, key=lambda item: item.gpu_id))
