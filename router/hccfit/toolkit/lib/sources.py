"""多源检索适配器：Europe PMC / PubMed / OpenAlex。

所有适配器返回 (list[ps.Paper], meta)：
    meta = {"source": ..., "query": ..., "hits": int, "calls": int, "error": str|None}

约定：
  - 只返回有标题 + 摘要的记录（ps.to_paper 会过滤），因为下游打分需要摘要。
  - 限流：PubMed 3 req/s；OpenAlex 1 req/s；EPMC 公平使用。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paper_search as ps  # noqa: E402

UA = {"User-Agent": "HCC-Fit-Agent/0.2 (mailto:research@example.org)"}
EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
OPENALEX = "https://api.openalex.org/works"


def _get(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            last = exc
            if attempt == 0:
                time.sleep(1.0)
    raise last


def _meta(source: str, query: str, hits: int, calls: int, error: str | None = None) -> dict:
    return {"source": source, "query": query, "hits": hits, "calls": calls, "error": error}


# ---------------------------------------------------------------- Europe PMC

def epmc(query: str, limit: int = 50, sort: str = "") -> tuple[list[ps.Paper], dict]:
    _throttle("epmc", 0.10)
    try:
        data = ps.fetch(query, sort, limit)
    except Exception as exc:
        return [], _meta("epmc", query, 0, 1, str(exc)[:160])
    papers = []
    for item in (data.get("resultList") or {}).get("result") or []:
        p = ps.to_paper(item)
        if p:
            papers.append(p)
    return papers, _meta("epmc", query, int(data.get("hitCount") or 0), 1)


# ---------------------------------------------------------------- PubMed

def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _pubmed_parse(xml_bytes: bytes) -> list[ps.Paper]:
    root = ET.fromstring(xml_bytes)
    out = []
    for art in root.findall(".//PubmedArticle"):
        cit = art.find("MedlineCitation")
        if cit is None:
            continue
        pmid = _text(cit.find("PMID"))
        article = cit.find("Article")
        if article is None:
            continue
        title = _text(article.find("ArticleTitle"))
        parts = []
        for ab in article.findall("Abstract/AbstractText"):
            label = ab.get("Label")
            body = _text(ab)
            parts.append(f"{label}: {body}" if label and body else body)
        abstract = " ".join(x for x in parts if x)
        journal = _text(article.find("Journal/Title"))
        year_txt = _text(article.find("Journal/JournalIssue/PubDate/Year"))
        if not year_txt:
            year_txt = _text(article.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4]
        try:
            year = int(year_txt)
        except ValueError:
            year = 0
        doi = ""
        for aid in list(article.findall("ArticleIdList/ArticleId")) + list(
                art.findall("PubmedData/ArticleIdList/ArticleId")):
            if aid.get("IdType") == "doi":
                doi = _text(aid).lower()
                break
        pubtypes = " ".join(_text(pt).lower() for pt in
                            article.findall("PublicationTypeList/PublicationType"))
        if not title or len(abstract) < 250:
            continue
        out.append(ps.Paper(
            pmid=pmid, doi=doi, title=title, abstract=abstract, journal=journal,
            year=year, cited=0, oa=False, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            pubtypes=pubtypes,
        ))
    return out


def pubmed(query: str, limit: int = 25) -> tuple[list[ps.Paper], dict]:
    calls = 0
    try:
        url = (PUBMED_ESEARCH + "?db=pubmed&retmode=json&sort=relevance&retmax="
               + str(limit) + "&term=" + urllib.parse.quote(query))
        _throttle("pubmed", 0.34)
        data = json.loads(_get(url))
        calls += 1
        ids = (data.get("esearchresult") or {}).get("idlist") or []
        hit_count = int((data.get("esearchresult") or {}).get("count") or 0)
        if not ids:
            return [], _meta("pubmed", query, hit_count, calls)
        _throttle("pubmed", 0.34)
        url = PUBMED_EFETCH + "?db=pubmed&retmode=xml&id=" + ",".join(ids)
        xml_bytes = _get(url)
        calls += 1
        papers = _pubmed_parse(xml_bytes)
        return papers, _meta("pubmed", query, hit_count, calls)
    except Exception as exc:
        return [], _meta("pubmed", query, 0, calls or 1, str(exc)[:160])


# ---------------------------------------------------------------- OpenAlex

def _openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pairs = []
    for word, positions in inv.items():
        for pos in positions or []:
            pairs.append((pos, word))
    return " ".join(word for _, word in sorted(pairs))


def openalex(query: str, limit: int = 25, year_min: int = 2018) -> tuple[list[ps.Paper], dict]:
    calls = 0
    for attempt in range(2):
        try:
            flt = f"from_publication_date:{year_min}-01-01"
            url = (OPENALEX + "?search=" + urllib.parse.quote(query)
                   + f"&per-page={limit}&filter={flt}&mailto=research@example.org")
            _throttle("openalex", 1.05)
            data = json.loads(_get(url))
            calls += 1
            papers = []
            for w in data.get("results") or []:
                abstract = _openalex_abstract(w.get("abstract_inverted_index"))
                title = (w.get("title") or "").strip()
                if not title or len(abstract) < 250:
                    continue
                src = ((w.get("primary_location") or {}).get("source") or {})
                journal = src.get("display_name") or ""
                doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
                try:
                    year = int(w.get("publication_year") or 0)
                except (TypeError, ValueError):
                    year = 0
                papers.append(ps.Paper(
                    pmid="", doi=doi, title=title, abstract=abstract, journal=journal,
                    year=year, cited=int(w.get("cited_by_count") or 0),
                    oa=bool((w.get("open_access") or {}).get("is_oa")),
                    url=((w.get("primary_location") or {}).get("landing_page_url")
                         or (f"https://doi.org/{doi}" if doi else "")),
                    pubtypes=str(w.get("type") or "").lower(),
                ))
            return papers, _meta("openalex", query, int((data.get("meta") or {}).get("count") or 0), calls)
        except Exception as exc:
            if "429" in str(exc) and attempt == 0:
                time.sleep(2.0)
                continue
            return [], _meta("openalex", query, 0, calls or 1, str(exc)[:160])
    return [], _meta("openalex", query, 0, calls or 1, "rate limited")


# ---------------------------------------------------------------- 统一入口

SOURCES = {"epmc": epmc, "pubmed": pubmed, "openalex": openalex}

_LOCK = threading.Lock()
_LAST = {"pubmed": 0.0, "openalex": 0.0, "epmc": 0.0}


def _throttle(name: str, interval: float) -> None:
    """跨线程限流：保证同一数据源的最小请求间隔。"""
    with _LOCK:
        wait = interval - (time.time() - _LAST[name])
        if wait > 0:
            time.sleep(wait)
        _LAST[name] = time.time()


def search(source: str, query: str, limit: int = 25, year_min: int = 2018, sort: str = ""):
    if source == "openalex":
        return openalex(query, limit, year_min)
    if source == "pubmed":
        return pubmed(query, limit)
    return epmc(query, limit, sort)


def merge(papers: list[ps.Paper]) -> list[ps.Paper]:
    """按 DOI → PMID → 归一化标题 去重；保留摘要更长的一条。"""
    import re

    def norm_title(t: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()[:120]

    by_key: dict[str, ps.Paper] = {}
    by_title: dict[str, str] = {}
    for p in papers:
        t = norm_title(p.title)
        key = p.doi or p.pmid or t
        if key in by_key:
            if len(p.abstract) > len(by_key[key].abstract):
                by_key[key] = p
            by_title[t] = key
            continue
        if t in by_title:
            old_key = by_title[t]
            if old_key in by_key and len(p.abstract) > len(by_key[old_key].abstract):
                by_key[old_key] = p
            continue
        by_key[key] = p
        by_title[t] = key
    return list(by_key.values())
