"""Wikipedia / Wikidata presence check (extends the GEO skill's brand_scanner).

The entity is matched by its Wikidata "official website" (P856) so that, e.g.,
apple.com resolves to "Apple Inc." rather than the fruit.
"""

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
ENTITY_HINTS = ("company", "brand", "manufacturer", "corporation", "chain", "business", "retailer", "conglomerate")
# Wikidata properties for official social accounts.
SOCIAL_PROPERTIES = {
    "youtube": "P2397", "linkedin": "P4264", "reddit": "P3984",
    "x": "P2002", "instagram": "P2003", "facebook": "P2013",
}
FOLLOWERS_PROPERTY = "P8687"  # "social media followers", qualified by the account property


@dataclass
class WikiPresence:
    wikidata_id: str = ""
    wikidata_claims: int = 0
    article_title: str = ""
    article_length: int = 0
    domain_citations: int = 0
    mention_hits: int = 0
    matched_by: str = ""
    social_accounts: dict[str, int] = field(default_factory=dict)
    youtube_subscribers: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def social_from_claims(claims: dict) -> tuple[dict[str, int], int]:
    """Count official social accounts and the largest YouTube subscriber figure on a Wikidata item."""
    accounts = {name: len(claims.get(prop, [])) for name, prop in SOCIAL_PROPERTIES.items() if claims.get(prop)}
    subscribers = 0
    for claim in claims.get(FOLLOWERS_PROPERTY, []):
        if SOCIAL_PROPERTIES["youtube"] not in claim.get("qualifiers", {}):
            continue
        amount = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("amount", "0")
        try:
            subscribers = max(subscribers, int(float(str(amount))))
        except ValueError:
            continue
    return accounts, subscribers


def _entity_richness(entity: dict) -> tuple[bool, int]:
    has_article = bool(entity.get("sitelinks", {}).get("enwiki"))
    return has_article, sum(len(v) for v in entity.get("claims", {}).values())


def _host(url: str) -> str:
    host = (urlparse(url if "://" in url else f"https://{url}").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def domains_match(site: str, domain: str) -> bool:
    host = _host(site)
    return bool(host) and (host == domain or host.endswith("." + domain) or domain.endswith("." + host))


def score_wikipedia(p: WikiPresence) -> tuple[int, str]:
    """Map Wikipedia/Wikidata signals to the brand-mentions rubric bands."""
    has_article = bool(p.article_title)
    has_wikidata = bool(p.wikidata_id)
    if has_article and p.article_length >= 40000 and p.wikidata_claims >= 50 and p.domain_citations >= 5:
        return 95, "Detailed article, rich Wikidata entry, site cited across Wikipedia"
    if has_article and p.article_length >= 20000 and has_wikidata:
        return 85 if p.domain_citations >= 3 else 78, "Substantial article with Wikidata entry"
    if has_article and has_wikidata and (p.article_length >= 8000 or p.mention_hits >= 2):
        return 70, "Article exists with Wikidata entry and mentions in other articles"
    if has_article:
        return 55, "Article exists (stub/start) with limited supporting signals"
    if p.domain_citations >= 1 or p.mention_hits >= 3:
        return 40 if has_wikidata else 35, "No article, but brand is cited or mentioned on Wikipedia"
    if has_wikidata or p.mention_hits >= 1:
        return 20, "Passing references only (Wikidata entry or a few mentions)"
    return 0, "No Wikipedia or Wikidata presence found"


class WikipediaChecker:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _get(self, url: str, params: dict) -> dict:
        response = await self.client.get(url, params={**params, "format": "json"}, timeout=15)
        response.raise_for_status()
        return response.json()

    async def _match_entity(self, brand: str, domains: tuple[str, ...], presence: WikiPresence) -> dict | None:
        search = await self._get(WIKIDATA_API, {
            "action": "wbsearchentities", "search": brand, "language": "en", "limit": 8, "type": "item",
        })
        ids = [item["id"] for item in search.get("search", [])]
        if not ids:
            return None
        entities = (await self._get(WIKIDATA_API, {
            "action": "wbgetentities", "ids": "|".join(ids), "props": "claims|sitelinks|descriptions",
        })).get("entities", {})

        website_matches = [
            entities.get(entity_id, {}) for entity_id in ids
            if any(
                isinstance(value, str) and any(domains_match(value, d) for d in domains)
                for value in (
                    c.get("mainsnak", {}).get("datavalue", {}).get("value", "")
                    for c in entities.get(entity_id, {}).get("claims", {}).get("P856", [])
                )
            )
        ]
        if website_matches:
            # Several items can list the same site (e.g. a stub "Sony" item and Sony Group):
            # prefer the one with an English article and the richest data.
            presence.matched_by = "official website (P856)"
            return max(website_matches, key=_entity_richness)
        for entity_id in ids:
            entity = entities.get(entity_id, {})
            description = entity.get("descriptions", {}).get("en", {}).get("value", "").lower()
            title = entity.get("sitelinks", {}).get("enwiki", {}).get("title", "")
            if brand.lower() in title.lower() and any(h in description for h in ENTITY_HINTS):
                presence.matched_by = "name + entity description"
                return entity
        return None

    async def check(self, brand: str, domain: str, alt_domains: tuple[str, ...] = ()) -> WikiPresence:
        """`alt_domains` covers geo-redirect targets (e.g. dyson.com.sg for dyson.com)."""
        presence = WikiPresence()
        try:
            entity = await self._match_entity(brand, (domain, *alt_domains), presence)
            if entity:
                presence.wikidata_id = entity.get("id", "")
                presence.wikidata_claims = sum(len(v) for v in entity.get("claims", {}).values())
                presence.social_accounts, presence.youtube_subscribers = social_from_claims(entity.get("claims", {}))
                presence.article_title = entity.get("sitelinks", {}).get("enwiki", {}).get("title", "")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Wikidata lookup failed for %s: %s", domain, exc)
            presence.errors.append("Wikidata lookup failed")

        try:
            if presence.article_title:
                info = await self._get(WIKIPEDIA_API, {
                    "action": "query", "prop": "info", "titles": presence.article_title, "redirects": 1,
                })
                for page in info.get("query", {}).get("pages", {}).values():
                    presence.article_length = int(page.get("length", 0))
            usage = await self._get(WIKIPEDIA_API, {
                "action": "query", "list": "exturlusage", "euquery": f"*.{domain}",
                "euprotocol": "", "eunamespace": 0, "eulimit": 500, "euprop": "title",
            })
            presence.domain_citations = len({u["title"] for u in usage.get("query", {}).get("exturlusage", [])})
            mentions = await self._get(WIKIPEDIA_API, {
                "action": "query", "list": "search", "srsearch": f'"{brand}"',
                "srlimit": 1, "srinfo": "totalhits", "srprop": "",
            })
            presence.mention_hits = int(mentions.get("query", {}).get("searchinfo", {}).get("totalhits", 0))
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Wikipedia lookup failed for %s: %s", domain, exc)
            presence.errors.append("Wikipedia lookup failed")
        return presence
