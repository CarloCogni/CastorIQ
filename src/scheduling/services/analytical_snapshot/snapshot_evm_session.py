# scheduling/services/analytical_snapshot/snapshot_evm_session.py
"""EVM compute session scoped to snapshot as-of date and recorded baseline."""

from __future__ import annotations

from datetime import date
from typing import Any

from scheduling.services.executive_controls.evm_compute_session import E8EVMComputeSession


class SnapshotEVMComputeSession(E8EVMComputeSession):
    """Single compute_evm() bound to snapshot manifest inputs."""

    def __init__(
        self,
        project_id: str,
        *,
        as_of_date: date,
        baseline_version_id: str | None = None,
    ) -> None:
        super().__init__(project_id)
        self._data_date = as_of_date
        self._is_p6 = True
        self._baseline_version_id = baseline_version_id

    def evm(self) -> dict[str, Any]:
        if self._evm is None:
            from scheduling.services.evm import compute_evm

            self._evm = compute_evm(
                self.project_id,
                self._data_date,
                baseline_version_id=self._baseline_version_id,
            )
        return self._evm
