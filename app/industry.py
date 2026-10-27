"""An industry and its category leader, as identified by AI for each audited brand.

There is no fixed catalogue: Claude identifies the brand's industry and that industry's leader from
the website URL and content (or from an industry the user types in), and the leader's site is then
audited with the same GEO pipeline for the benchmark. Claude also ranks the next strongest brands
(`alternates`), which are benchmarked instead when the leader's site blocks automated access.
"""

from dataclasses import dataclass, replace

from app.scoring.signals import registrable_domain


@dataclass(frozen=True)
class Industry:
    name: str
    name_zh: str
    leader: str
    leader_zh: str
    leader_url: str
    alternates: tuple[dict, ...] = ()  # runner-ups in rank order: {leader, leader_zh, leader_url}

    @property
    def leader_domain(self) -> str:
        return registrable_domain(self.leader_url)

    def is_leader(self, domain: str) -> bool:
        return domain == self.leader_domain

    def to_dict(self) -> dict:
        return {"name": self.name, "name_zh": self.name_zh, "leader": self.leader,
                "leader_zh": self.leader_zh, "leader_url": self.leader_url, "alternates": list(self.alternates)}

    def candidates(self) -> list[dict]:
        """The leader, then the runner-ups, de-duplicated by domain."""
        seen: set[str] = set()
        ranked = []
        for candidate in [{"leader": self.leader, "leader_zh": self.leader_zh, "leader_url": self.leader_url},
                          *self.alternates]:
            domain = registrable_domain(candidate["leader_url"])
            if domain not in seen:
                seen.add(domain)
                ranked.append(candidate)
        return ranked

    def with_leader(self, candidate: dict) -> "Industry":
        """This industry benchmarked against another candidate (a runner-up standing in for the leader)."""
        return replace(self, leader=candidate["leader"], leader_zh=candidate.get("leader_zh") or candidate["leader"],
                       leader_url=candidate["leader_url"])

    @classmethod
    def from_dict(cls, data: dict) -> "Industry":
        return cls(data["name"], data.get("name_zh") or data["name"], data["leader"],
                   data.get("leader_zh") or data["leader"], data["leader_url"],
                   tuple(data.get("alternates") or ()))


def resolve_industry(name: str | None, *candidates: dict) -> Industry | None:
    """The industry with this name among those stored with an audit (AI-identified or typed in)."""
    if not name:
        return None
    match = next((c for c in candidates if c and c.get("name", "").lower() == name.lower()), None)
    return Industry.from_dict(match) if match else None
