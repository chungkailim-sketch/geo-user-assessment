"""A scored rubric item and helpers to total rubric items into 0–100 category scores."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Check:
    group: str  # rubric section, e.g. "安全性"
    name: str
    points: float
    earned: float | None  # None = could not be measured from outside the site
    detail: str = ""

    @property
    def measured(self) -> bool:
        return self.earned is not None

    @property
    def status(self) -> str:
        if self.earned is None:
            return "未检测"
        ratio = self.earned / self.points if self.points else 1
        return "通过" if ratio >= 0.8 else "部分" if ratio >= 0.5 else "未通过"

    def to_dict(self) -> dict:
        return {**asdict(self), "status": self.status}


def score_checks(checks: list[Check]) -> float:
    """Points earned over points measurable, as 0–100."""
    possible = sum(c.points for c in checks if c.measured)
    if not possible:
        return 0.0
    return round(100 * sum(c.earned for c in checks if c.measured) / possible, 1)


def group_scores(checks: list[Check]) -> list[dict]:
    """Per-group earned/possible, in first-seen order."""
    groups: dict[str, dict] = {}
    for check in checks:
        group = groups.setdefault(check.group, {"group": check.group, "earned": 0.0, "possible": 0.0, "max": 0.0})
        group["max"] += check.points
        if check.measured:
            group["earned"] += check.earned
            group["possible"] += check.points
    for group in groups.values():
        group["earned"] = round(group["earned"], 1)
        group["score"] = round(100 * group["earned"] / group["possible"], 1) if group["possible"] else None
    return list(groups.values())


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))
