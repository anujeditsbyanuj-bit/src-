import json
import re
import aiohttp
import asyncio
from typing import Optional

# Community-maintained shortener domain sources
SOURCES = [
    {
        "name": "DontPokeMe_active",
        "url": "https://raw.githubusercontent.com/DontPokeMe/known-link-shorteners/main/data/shorteners.json",
        "format": "dontpokeme_json",
        "expected_status": "active",
    },
    {
        "name": "DontPokeMe_inactive",
        "url": "https://raw.githubusercontent.com/DontPokeMe/known-link-shorteners/main/data/inactive.json",
        "format": "inactive_json",
        "expected_status": "inactive",
    },
    {
        "name": "PeterDaveHello_active",
        "url": "https://raw.githubusercontent.com/PeterDaveHello/url-shorteners/master/list",
        "format": "domain_list",
        "expected_status": "active",
    },
    {
        "name": "PeterDaveHello_inactive",
        "url": "https://raw.githubusercontent.com/PeterDaveHello/url-shorteners/master/inactive",
        "format": "domain_list",
        "expected_status": "inactive",
    },
]

TIMEOUT_SEC = 30


def _clean_domain(raw: str) -> Optional[str]:
    raw = raw.strip().lower()
    raw = re.sub(r"^https?://", "", raw)
    raw = raw.split("/")[0]
    raw = raw.split("@")[-1]
    raw = raw.split(":")[0]
    if not raw or raw.startswith("#") or raw.startswith(";"):
        return None
    if "." not in raw:
        return None
    return raw


STATUS_MAP = {
    "active": "active",
    "alive": "active",
    "defunct": "inactive",
    "dead": "inactive",
    "inactive": "inactive",
    "malicious": "malicious",
    "malware": "malicious",
    "phishing": "malicious",
    "parked": "inactive",
    "deprecated": "deprecated",
    "unknown": "unknown",
    "suspicious": "malicious",
    "probable": "active",
}


def _normalize_status(raw: str) -> str:
    return STATUS_MAP.get(raw.strip().lower(), "unknown")


def _parse_dontpokeme_json(raw_text: str) -> list[dict]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []

    results = []
    entries = data if isinstance(data, list) else data.get("shorteners", data.get("domains", []))
    for entry in entries:
        if isinstance(entry, str):
            domain = entry.strip().lower()
            results.append({
                "domain": domain,
                "status": "active",
                "type": "shortener",
                "notes": "",
                "source": "dontpokeme",
            })
            continue
        domain = entry.get("domain", "").strip().lower()
        if not domain:
            continue
        status = _normalize_status(entry.get("status", "unknown"))
        notes = entry.get("notes", "")
        type_ = entry.get("type", "shortener")
        results.append({
            "domain": domain,
            "status": status,
            "type": type_,
            "notes": notes,
            "source": "dontpokeme",
        })
    return results


def _parse_inactive_json(raw_text: str) -> list[dict]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    results = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                results.append({
                    "domain": item.strip().lower(),
                    "status": "inactive",
                    "type": "shortener",
                    "notes": "",
                    "source": "dontpokeme",
                })
            elif isinstance(item, dict):
                domain = item.get("domain", "").strip().lower()
                if domain:
                    results.append({
                        "domain": domain,
                        "status": "inactive",
                        "type": item.get("type", "shortener"),
                        "notes": item.get("notes", ""),
                        "source": "dontpokeme",
                    })
    elif isinstance(data, dict):
        for key, val in data.items():
            domain = key.strip().lower()
            if domain and "." in domain:
                notes = val if isinstance(val, str) else json.dumps(val) if val else ""
                results.append({
                    "domain": domain,
                    "status": "inactive",
                    "type": "shortener",
                    "notes": notes,
                    "source": "dontpokeme",
                })
    return results


def _parse_domain_list(raw_text: str) -> list[dict]:
    results = []
    for line in raw_text.splitlines():
        domain = _clean_domain(line)
        if domain:
            results.append(
                {
                    "domain": domain,
                    "status": "active",
                    "type": "shortener",
                    "notes": "",
                    "source": "peterdavehello",
                }
            )
    return results


PARSERS = {
    "dontpokeme_json": _parse_dontpokeme_json,
    "inactive_json": _parse_inactive_json,
    "domain_list": _parse_domain_list,
}


async def fetch_source(session: aiohttp.ClientSession, source: dict) -> list[dict]:
    name = source["name"]
    url = source["url"]
    parser = PARSERS.get(source["format"])
    expected_status = source["expected_status"]

    if not parser:
        return []

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC)) as resp:
            if resp.status != 200:
                return []
            text = await resp.text()
    except Exception:
        return []

    domains = parser(text)
    for d in domains:
        if d["status"] == "active":
            d["status"] = expected_status
    return domains


async def fetch_all_sources() -> list[dict]:
    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_source(session, s) for s in SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_domains: list[dict] = []
    seen = set()
    for result in results:
        if isinstance(result, Exception):
            continue
        for d in result:
            key = d["domain"]
            if key not in seen:
                seen.add(key)
                all_domains.append(d)
            else:
                for existing in all_domains:
                    if existing["domain"] == key:
                        if d["status"] == "inactive" and existing["status"] != "inactive":
                            existing["status"] = "inactive"
                            existing["notes"] += f"; also in {d['source']}"
                        break
    return all_domains
