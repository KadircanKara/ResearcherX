"""Build an on-topic arXiv corpus for `bulk_ingest_arxiv`: UAVs and mobile robots only.

Run (from /app, the `-m` form is required, see bulk_ingest_arxiv):

    docker compose exec -T backend python -m scripts.fetch_arxiv_corpus \\
        search --out /app/stub-papers/arxiv-uav --target 2000
    docker compose exec -T backend python -m scripts.fetch_arxiv_corpus \\
        download --out /app/stub-papers/arxiv-uav --batch 500

`search` writes `<out>/corpus.json` in the record shape `bulk_ingest_arxiv`
reads, and downloads nothing. `download` fetches the next `--batch` PDFs that
are neither on disk nor in `<out>/fetched.txt`. The log is what makes the
batch loop work: `bulk_ingest_arxiv --present-only --delete-after` removes
each PDF once its chunks are stored, and without the log the next `download`
would fetch it again.

Topic control is two layers. The queries pick the neighbourhood (multi-UAV
planning, multi-robot exploration, MARL for robot teams, ...). `on_topic` then
admits a paper only when it names a UAV or mobile-robot platform in its TITLE,
or at least twice in its abstract, so a MARL paper benchmarked on StarCraft or
a manipulator paper that mentions drones once in passing is dropped. Accepted
papers are interleaved round-robin across queries, so no single topic fills
the target.

Metadata: authors, year and title come from arXiv. Semantic Scholar's batch
endpoint fills `venue` (and the publication year) when it knows the paper;
`metadata_source` records which source answered, "s2" or "arxiv".

arXiv asks for one request every 3 seconds; both phases hold that pace.
"""

import argparse
import itertools
import json
import math
import pathlib
import re
import sys
import time
import xml.etree.ElementTree as ET

import httpx

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF = "https://arxiv.org/pdf/{}"
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
_UA = {"User-Agent": "ResearcherX-benchmark/1.0 (corpus builder; 1 req/3s)"}
_DELAY_S = 3.0
_PAGE = 200
_S2_CHUNK = 500  # S2 batch endpoint maximum
_PDF_MAX_BYTES = 50 * 1024 * 1024

_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Categories where UAV / mobile-robot work is published. cs.NI / eess.SP / cs.IT
# carry the UAV-communications literature the existing corpus already holds.
_CATEGORIES = ("cs.RO", "cs.MA", "cs.AI", "cs.LG", "eess.SY", "cs.SY", "cs.NI", "eess.SP", "cs.IT")

# (label, arXiv search expression). Each is ANDed with the category filter.
QUERIES: tuple[tuple[str, str], ...] = (
    ("multi-uav-path-planning", 'abs:"multi-UAV" AND abs:"path planning"'),
    ("uav-swarm", 'abs:"UAV swarm" OR abs:"drone swarm"'),
    ("uav-search-rescue", '(abs:UAV OR abs:drone) AND abs:"search and rescue"'),
    (
        "uav-target-search",
        '(abs:UAV OR abs:drone) AND (abs:"target search" OR abs:"target tracking")',
    ),
    ("uav-task-allocation", '(abs:UAV OR abs:drone) AND abs:"task allocation"'),
    ("uav-coverage", '(abs:UAV OR abs:drone) AND abs:"coverage path planning"'),
    ("uav-trajectory-comms", 'abs:UAV AND abs:"trajectory optimization" AND abs:communication'),
    ("uav-assisted-networks", 'abs:"UAV-assisted" OR abs:"UAV-enabled"'),
    ("uav-relay-connectivity", "abs:UAV AND (abs:relay OR abs:connectivity) AND abs:network"),
    ("uav-collision-avoidance", '(abs:UAV OR abs:quadrotor) AND abs:"collision avoidance"'),
    ("uav-surveillance", "(abs:UAV OR abs:drone) AND (abs:surveillance OR abs:monitoring)"),
    ("drone-delivery-routing", 'abs:drone AND (abs:delivery OR abs:"vehicle routing")'),
    ("marl-uav", 'abs:"multi-agent reinforcement learning" AND (abs:UAV OR abs:drone)'),
    ("marl-robots", 'abs:"multi-agent reinforcement learning" AND abs:robot'),
    (
        "drl-robot-navigation",
        'abs:"reinforcement learning" AND abs:"mobile robot" AND abs:navigation',
    ),
    ("multi-robot-exploration", 'abs:"multi-robot" AND abs:exploration'),
    ("multi-robot-coverage", 'abs:"multi-robot" AND abs:coverage'),
    ("multi-robot-task-allocation", 'abs:"multi-robot" AND abs:"task allocation"'),
    (
        "multi-robot-path-planning",
        'abs:"multi-robot" AND (abs:"path planning" OR abs:"motion planning")',
    ),
    ("multi-robot-slam", 'abs:"multi-robot" AND abs:SLAM'),
    (
        "formation-control",
        '(abs:"multi-robot" OR abs:UAV OR abs:quadrotor) AND abs:"formation control"',
    ),
    ("air-ground-teams", '(abs:UAV OR abs:drone) AND (abs:UGV OR abs:"ground robot")'),
    (
        "mobile-robot-planning",
        'abs:"mobile robot" AND (abs:"path planning" OR abs:"motion planning")',
    ),
    ("swarm-robotics", 'abs:"swarm robotics" OR abs:"robot swarm"'),
)

# A named UAV or mobile-robot PLATFORM. Generic "robot" is deliberately absent:
# it admits manipulators and humanoid-arm work. Self-driving cars are absent too.
_PLATFORM_RE = re.compile(
    r"\b(?:"
    r"uavs?|uass?|suas|drones?|unmanned aerial|unmanned aircraft|"
    r"aerial (?:vehicles?|robots?|robotics|swarms?|platforms?)|"
    r"quad-?rotors?|quad-?copters?|multi-?rotors?|multi-?copters?|"
    r"mobile robot(?:s|ic|ics)?|multi-?robots?|robot(?:ic)? swarms?|swarm robotics|"
    r"robot teams?|teams? of (?:mobile )?robots|ground robots?|"
    r"ugvs?|agvs?|automated guided vehicles?|unmanned ground vehicles?|"
    r"legged robots?|quadruped(?:al)? robots?|wheeled robots?|rovers?|"
    r"unmanned surface vehicles?|usvs?|auvs?|autonomous underwater vehicles?"
    r")\b",
    re.IGNORECASE,
)


def on_topic(title: str, abstract: str) -> bool:
    """A platform named in the title, or at least twice in the abstract."""
    if _PLATFORM_RE.search(title):
        return True
    return len(_PLATFORM_RE.findall(abstract)) >= 2


def bare_id(entry_id: str) -> str:
    """`http://arxiv.org/abs/2409.03245v2` -> `2409.03245` (old-style ids keep their slash)."""
    tail = entry_id.split("/abs/", 1)[-1]
    return re.sub(r"v\d+$", "", tail)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def parse_feed(xml_text: str) -> tuple[int, list[dict]]:
    """(totalResults, records) from one arXiv Atom page."""
    root = ET.fromstring(xml_text)
    total_el = root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
    total = int(total_el.text) if total_el is not None and total_el.text else 0
    records = []
    for entry in root.findall("a:entry", _NS):
        id_el = entry.find("a:id", _NS)
        if id_el is None or not id_el.text or "/abs/" not in id_el.text:
            continue  # arXiv returns an id-less error entry for a bad query
        published = _clean(entry.findtext("a:published", default="", namespaces=_NS))
        records.append(
            {
                "arxiv_id": bare_id(id_el.text.strip()),
                "title": _clean(entry.findtext("a:title", default="", namespaces=_NS)),
                "abstract": _clean(entry.findtext("a:summary", default="", namespaces=_NS)),
                "authors": [
                    _clean(a.findtext("a:name", default="", namespaces=_NS))
                    for a in entry.findall("a:author", _NS)
                ],
                "year": int(published[:4]) if published[:4].isdigit() else None,
                "journal_ref": _clean(
                    entry.findtext("arxiv:journal_ref", default="", namespaces=_NS)
                )
                or None,
                "categories": [c.get("term") for c in entry.findall("a:category", _NS)],
            }
        )
    return total, records


def interleave(per_query: dict[str, list[dict]], target: int) -> list[dict]:
    """Round-robin across queries, first occurrence of an id wins, stop at target."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in itertools.zip_longest(*per_query.values()):
        for rec in row:
            if rec is None or rec["arxiv_id"] in seen:
                continue
            seen.add(rec["arxiv_id"])
            out.append(rec)
            if len(out) >= target:
                return out
    return out


def pdf_name(arxiv_id: str) -> str:
    """Matches bulk_ingest_arxiv's lookup: old-style `math/0101001` loses its slash."""
    return f"{arxiv_id.replace('/', '_')}.pdf"


def _get(client: httpx.Client, url: str, **kw) -> httpx.Response:
    """GET with polite backoff on 429/503 and transport errors."""
    for attempt in range(1, 6):
        try:
            resp = client.get(url, **kw)
        except httpx.TransportError:
            if attempt == 5:
                raise
            time.sleep(_DELAY_S * 2 * attempt)
            continue
        if resp.status_code in (429, 503) and attempt < 5:
            retry_after = resp.headers.get("Retry-After", "")
            time.sleep(float(retry_after) if retry_after.isdigit() else _DELAY_S * 4 * attempt)
            continue
        return resp
    raise RuntimeError("unreachable")


def _search_query(client: httpx.Client, label: str, expr: str, max_results: int) -> list[dict]:
    cats = " OR ".join(f"cat:{c}" for c in _CATEGORIES)
    query = f"({expr}) AND ({cats})"
    accepted: list[dict] = []
    start = 0
    while start < max_results:
        params = {
            "search_query": query,
            "start": start,
            "max_results": _PAGE,
            "sortBy": "relevance",
        }
        resp = _get(client, ARXIV_API, params=params)
        time.sleep(_DELAY_S)
        resp.raise_for_status()
        total, records = parse_feed(resp.text)
        if not records:
            # arXiv occasionally answers an in-range page with zero entries;
            # one retry, then treat the query as exhausted.
            if start < total:
                resp = _get(client, ARXIV_API, params=params)
                time.sleep(_DELAY_S)
                total, records = parse_feed(resp.text)
            if not records:
                break
        for rec in records:
            if on_topic(rec["title"], rec["abstract"]):
                rec["topic"] = label
                accepted.append(rec)
        start += _PAGE
        if start >= total:
            break
    print(f"  {label:<28} {len(accepted):>4} on topic", flush=True)
    return accepted


def _s2_enrich(client: httpx.Client, records: list[dict]) -> int:
    """Fill venue/year from Semantic Scholar where it knows the paper. Best effort."""
    hits = 0
    for i in range(0, len(records), _S2_CHUNK):
        chunk = records[i : i + _S2_CHUNK]
        data = None
        for attempt in range(1, 6):
            try:
                resp = client.post(
                    S2_BATCH,
                    params={"fields": "venue,year"},
                    json={"ids": [f"ARXIV:{r['arxiv_id']}" for r in chunk]},
                    timeout=60,
                )
            except httpx.TransportError:
                resp = None
            if resp is not None and resp.status_code == 200:
                data = resp.json()
                break
            time.sleep(5 * attempt)
        if data is None:
            print(f"  s2: chunk {i // _S2_CHUNK} failed, keeping arXiv metadata", file=sys.stderr)
            continue
        for rec, s2 in zip(chunk, data, strict=True):
            if not s2:
                continue
            hits += 1
            rec["metadata_source"] = "s2"
            if s2.get("venue"):
                rec["venue"] = s2["venue"]
            if s2.get("year"):
                rec["year"] = s2["year"]
        time.sleep(1.0)
    return hits


def cmd_search(out: pathlib.Path, target: int, force: bool) -> None:
    corpus = out / "corpus.json"
    if corpus.exists() and not force:
        sys.exit(f"{corpus} exists; pass --force to rebuild it (fetched.txt is kept)")
    out.mkdir(parents=True, exist_ok=True)
    # Over-fetch per query so the round-robin still reaches the target after
    # the topic filter and cross-query duplicates thin each list.
    per_query_cap = math.ceil(target / len(QUERIES) * 3 / _PAGE) * _PAGE
    print(f"searching {len(QUERIES)} queries, up to {per_query_cap} results each", flush=True)
    per_query: dict[str, list[dict]] = {}
    with httpx.Client(headers=_UA, timeout=60, follow_redirects=True) as client:
        for label, expr in QUERIES:
            per_query[label] = _search_query(client, label, expr, per_query_cap)
        records = interleave(per_query, target)
        for rec in records:
            rec["metadata_source"] = "arxiv"
            rec["venue"] = rec.pop("journal_ref") or "arXiv"
            rec["pdf_path"] = pdf_name(rec["arxiv_id"])
        hits = _s2_enrich(client, records)
    corpus.write_text(json.dumps(records, indent=1, ensure_ascii=False))
    print(f"\nwrote {len(records)} records to {corpus} ({hits} enriched by Semantic Scholar)")
    if len(records) < target:
        print(f"WARNING: only {len(records)} of {target} on-topic papers found", file=sys.stderr)


def cmd_download(out: pathlib.Path, batch: int) -> None:
    records = json.loads((out / "corpus.json").read_text())
    log = out / "fetched.txt"
    fetched = set(log.read_text().split()) if log.exists() else set()
    pending = [
        r
        for r in records
        if r["arxiv_id"] not in fetched and not (out / pdf_name(r["arxiv_id"])).exists()
    ][:batch]
    print(
        f"{len(records)} in corpus, {len(fetched)} fetched before, downloading {len(pending)}",
        flush=True,
    )
    failures: list[tuple[str, str]] = []
    with (
        httpx.Client(headers=_UA, timeout=120, follow_redirects=True) as client,
        log.open("a") as log_f,
    ):
        for i, rec in enumerate(pending, 1):
            arxiv_id = rec["arxiv_id"]
            dest = out / pdf_name(arxiv_id)
            try:
                resp = _get(client, ARXIV_PDF.format(arxiv_id))
                resp.raise_for_status()
                body = resp.content
                if not body.startswith(b"%PDF"):
                    raise ValueError("not a PDF")
                if len(body) > _PDF_MAX_BYTES:
                    raise ValueError(f"{len(body)} bytes exceeds cap")
                part = dest.with_suffix(".part")
                part.write_bytes(body)
                part.replace(dest)
                log_f.write(arxiv_id + "\n")
                log_f.flush()
                print(f"[{i}/{len(pending)}] {len(body) / 1e6:5.1f} MB  {arxiv_id}", flush=True)
            except Exception as exc:
                failures.append((arxiv_id, f"{type(exc).__name__}: {str(exc)[:100]}"))
                print(f"[{i}/{len(pending)}] FAIL  {arxiv_id}  {type(exc).__name__}", flush=True)
            time.sleep(_DELAY_S)
    print(f"\ndownloaded {len(pending) - len(failures)}, failed {len(failures)}")
    for arxiv_id, err in failures:
        print(f"  failed: {arxiv_id}  {err}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an on-topic arXiv corpus (UAVs / mobile robots)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="query arXiv, filter, write corpus.json")
    s.add_argument("--out", required=True, type=pathlib.Path)
    s.add_argument("--target", type=int, default=2000)
    s.add_argument("--force", action="store_true")
    d = sub.add_parser("download", help="fetch the next batch of PDFs")
    d.add_argument("--out", required=True, type=pathlib.Path)
    d.add_argument("--batch", type=int, default=500)
    args = parser.parse_args()
    if args.cmd == "search":
        cmd_search(args.out, args.target, args.force)
    else:
        cmd_download(args.out, args.batch)


if __name__ == "__main__":
    main()
