from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TaskResult:
    task_id: str
    platform: str
    account_name: str
    success: bool
    message: str
    output_path: str = ""
    capture_path: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    status: str = ""

    def __post_init__(self) -> None:
        if not self.status:
            self.status = "success" if self.success else "failed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
