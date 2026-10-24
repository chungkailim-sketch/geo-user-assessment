"""Compare a brand's audit with its industry leader's audit."""

from app.industry import Industry


def leader_summary(result: dict) -> dict:
    return {
        "brand_name": result["brand_name"],
        "url": result["url"],
        "score": result["score"],
        "label": result["label"],
        "citability": result["citability"]["score"],
        "authority": result["authority"]["score"],
        "citability_subscores": result["citability"]["subscores"],
        "platforms": {p["key"]: p["score"] for p in result["authority"]["platforms"]},
        "audited_at": result["audited_at"],
    }


def compare(brand: dict, leader: dict | None, industry: Industry, reason: str | None = None) -> dict:
    base = {
        "industry": industry.name,
        "leader": industry.leader,
        "leader_url": industry.leader_url,
        "is_leader": industry.is_leader(brand["domain"]),
    }
    if base["is_leader"]:
        return {**base, "status": "ok", "leader_scores": leader_summary(brand), "gaps": _gaps(brand, brand)}
    if leader is None:
        return {**base, "status": "unavailable", "reason": reason, "leader_scores": None, "gaps": None}
    return {**base, "status": "ok", "leader_scores": leader_summary(leader), "gaps": _gaps(brand, leader)}


def _gaps(brand: dict, leader: dict) -> dict:
    gaps = {
        "overall": round(brand["score"] - leader["score"], 1),
        "citability": round(brand["citability"]["score"] - leader["citability"]["score"], 1),
        "authority": round(brand["authority"]["score"] - leader["authority"]["score"], 1),
    }
    leader_platforms = {p["key"]: p["score"] for p in leader["authority"]["platforms"]}
    gaps["platforms"] = {
        p["key"]: p["score"] - leader_platforms.get(p["key"], 0) for p in brand["authority"]["platforms"]
    }
    return gaps
