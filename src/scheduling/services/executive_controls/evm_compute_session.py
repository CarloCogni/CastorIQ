# scheduling/services/executive_controls/evm_compute_session.py
"""Request-scoped single compute_evm() — shared by E8-D services."""

from __future__ import annotations

from datetime import date
from typing import Any

from scheduling.services.utils import get_project_data_date


class E8EVMComputeSession:
    """Ensure compute_evm() runs at most once per E8-D request scope."""

    def __init__(self, project_id: str) -> None:
        self.project_id = str(project_id)
        self._evm: dict[str, Any] | None = None
        self._data_date: date | None = None
        self._is_p6: bool = False

    @property
    def data_date(self) -> date:
        if self._data_date is None:
            self._data_date, self._is_p6 = get_project_data_date(self.project_id)
        return self._data_date

    @property
    def data_date_is_p6(self) -> bool:
        if self._data_date is None:
            _ = self.data_date
        return self._is_p6

    def evm(self) -> dict[str, Any]:
        """Return cached EVM payload from compute_evm()."""
        if self._evm is None:
            from scheduling.services.evm import compute_evm

            self._evm = compute_evm(self.project_id, as_of_date=self.data_date)
        return self._evm
