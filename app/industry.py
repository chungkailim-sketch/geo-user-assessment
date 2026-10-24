"""Industry catalogue and keyword-based industry detection."""

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from app.config import ROOT_DIR
from app.scoring.signals import registrable_domain

INDUSTRIES_PATH = ROOT_DIR / "config" / "industries.json"


@dataclass(frozen=True)
class Industry:
    name: str
    leader: str
    leader_url: str
    keywords: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    name_zh: str = ""
    leader_zh: str = ""
    custom: bool = False  # identified by AI for a brand outside the default catalogue

    @property
    def leader_domain(self) -> str:
        return registrable_domain(self.leader_url)

    def is_leader(self, domain: str) -> bool:
        return domain == self.leader_domain or domain in self.aliases

    def to_dict(self) -> dict:
        return {"name": self.name, "name_zh": self.name_zh, "leader": self.leader,
                "leader_zh": self.leader_zh, "leader_url": self.leader_url}

    @classmethod
    def from_custom(cls, data: dict) -> "Industry":
        return cls(data["name"], data["leader"], data["leader_url"], (), (),
                   data.get("name_zh") or data["name"], data.get("leader_zh") or data["leader"], custom=True)


@lru_cache
def load_industries() -> tuple[Industry, ...]:
    data = json.loads(INDUSTRIES_PATH.read_text(encoding="utf-8"))
    return tuple(
        Industry(
            i["name"], i["leader"], i["leader_url"],
            tuple(k.lower() for k in i["keywords"]), tuple(i.get("aliases", [])),
            i.get("name_zh", i["name"]), i.get("leader_zh", i["leader"]),
        )
        for i in data["industries"]
    )


def get_industry(name: str | None) -> Industry | None:
    if not name:
        return None
    return next((i for i in load_industries() if i.name.lower() == name.lower()), None)


def resolve_industry(name: str | None, *customs: dict) -> Industry | None:
    """A catalogue industry by name, else one of the custom industries stored with an audit."""
    if found := get_industry(name):
        return found
    if not name:
        return None
    custom = next((c for c in customs if c and c.get("name", "").lower() == name.lower()), None)
    return Industry.from_custom(custom) if custom else None


def industry_for_leader_domain(domain: str) -> Industry | None:
    return next((i for i in load_industries() if i.is_leader(domain)), None)


def keyword_scores(text: str) -> dict[str, int]:
    lowered = text.lower()
    scores = {}
    for industry in load_industries():
        scores[industry.name] = sum(
            len(re.findall(r"(?<![a-z])" + re.escape(keyword) + r"(?![a-z])", lowered))
            for keyword in industry.keywords
        )
    return scores


@dataclass(frozen=True)
class Detection:
    industry: str
    method: str
    scores: dict


def detect_industry(domain: str, weighted_text: str) -> Detection | None:
    """Detect by leader domain first, then keyword hits. Returns None when ambiguous."""
    leader = industry_for_leader_domain(domain)
    if leader:
        return Detection(leader.name, "leader domain", {})
    scores = keyword_scores(weighted_text)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if best_score == 0 or best_score < runner_up * 1.25:
        return Detection(best, "ambiguous", scores) if best_score else None
    return Detection(best, "keywords", scores)
