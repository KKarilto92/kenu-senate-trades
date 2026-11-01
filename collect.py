#!/usr/bin/env python3
"""Reads the Senate's periodic transaction reports from 1 January 2025 into this repository.

Runs on GitHub's computers, because efdsearch.senate.gov refuses visitors from outside the US. Each run:
  1. accepts the site's notice (KENU has read it; the run accepts it on his behalf);
  2. lists every periodic transaction report from START;
  3. reads each report not already saved: an electronic report as data, a paper report as its page images;
  4. writes data/index.json and last_run.json. The workflow then commits them, on every run.

What it writes:
  data/index.json             every report the search listed: id, kind, name, title, filed date, link
  data/reports/<id>.json      one report: the header, and an electronic report's table rows as the site shows them
  data/reports/<id>.html      the saved page of an electronic report, unchanged
  data/paper/<id>/p0001.gif   a paper report's page images, beside its saved page.html
  last_run.json               what this run did

The repository holds only public Senate records and this collector. No credentials: the workflow's own GitHub token commits.
A refusal is recorded in last_run.json and ends the run. It is never worked around.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import re
import sys
import time

import requests

ROOT = "https://efdsearch.senate.gov"
HOME = ROOT + "/search/home/"
SEARCH = ROOT + "/search/"
DATA = ROOT + "/search/report/data/"
START = dt.date(2025, 1, 1)
PTR_TYPE = "[11]"                     # the search form's value for Periodic Transactions
PAGE = 100
PAUSE_S = 2.0                         # between requests: gently
RETRIES = 3
BACKOFF_S = 30
MAX_RUN_S = 100 * 60                  # the workflow's limit is 120 minutes: stop reading in time to commit
USER_AGENT = "kenu-senate-trades collector (github.com/KKarilto92/kenu-senate-trades)"
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "data"
LAST = HERE / "last_run.json"


class Refused(RuntimeError):
    """The site said no. Recorded and stopped, never worked around."""


class Client:
    def __init__(self, session=None, pause=time.sleep, clock=time.monotonic):
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.pause, self.clock = pause, clock
        self.requests = 0

    def call(self, method: str, url: str, **kw):
        last = None
        for attempt in range(RETRIES):
            if self.requests:
                self.pause(PAUSE_S)
            self.requests += 1
            try:
                r = self.s.request(method, url, timeout=60, **kw)
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"
                self.pause(BACKOFF_S * (attempt + 1))
                continue
            if r.status_code in (401, 403, 451):
                raise Refused(f"HTTP {r.status_code} from {url}: {snippet(r.text)}")
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code} from {url}: {snippet(r.text)}"
                self.pause(BACKOFF_S * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(last or f"no answer from {url}")

    def accept_notice(self) -> None:
        r = self.call("GET", HOME)
        if "prohibition_agreement" not in r.text:
            raise Refused(f"no notice form at {HOME}: {snippet(r.text)}")
        token = form_token(r.text)
        r = self.call("POST", HOME, data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token},
                      headers={"Referer": HOME})
        if "prohibition_agreement" in r.text and "search" not in r.url:
            raise Refused(f"the notice was not accepted: {snippet(r.text)}")

    def token(self) -> str:
        return self.s.cookies.get("csrftoken") or self.s.cookies.get("csrf") or ""

    def search(self, since: dt.date) -> list:
        """Every row the search lists from `since`, a page at a time."""
        rows, start = [], 0
        while True:
            r = self.call("POST", DATA, headers={"Referer": SEARCH, "X-CSRFToken": self.token()}, data={
                "start": str(start), "length": str(PAGE), "report_types": PTR_TYPE, "filer_types": "[]",
                "submitted_start_date": since.strftime("%m/%d/%Y 00:00:00"), "submitted_end_date": "",
                "candidate_state": "", "senator_state": "", "office_id": "", "first_name": "", "last_name": "",
                "csrfmiddlewaretoken": self.token()})
            try:
                j = r.json()
            except ValueError:
                raise Refused(f"the search did not answer with data: {snippet(r.text)}") from None
            got = j.get("data") or []
            rows += got
            start += PAGE
            if not got or start >= int(j.get("recordsFiltered") or j.get("recordsTotal") or 0):
                return rows


def snippet(text: str, n: int = 300) -> str:
    return re.sub(r"\s+", " ", text or "")[:n]


def form_token(page: str) -> str:
    m = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', page)
    if not m:
        raise Refused(f"no form token on the notice page: {snippet(page)}")
    return m.group(1)


def cell(c: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()


def iso(d: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (d or "").strip())
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None


def report_of(row: list) -> dict:
    """One search row: first name, last name, office, the report's link, the date filed."""
    first, last, office, link, filed = row[:5]
    href = re.search(r'href="([^"]+)"', link).group(1)
    kind = "paper" if "/paper/" in href else "electronic" if "/ptr/" in href else "other"
    return {"id": href.rstrip("/").split("/")[-1], "kind": kind, "first": cell(first), "last": cell(last),
            "office": cell(office), "title": cell(link), "filed": iso(cell(filed)), "url": ROOT + href}


def table_rows(page: str) -> tuple[list, list]:
    """An electronic report's table: the header cells and the rows' cells, as the site shows them."""
    head = [cell(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", page.split("<tbody>", 1)[0], re.S)]
    if "<tbody>" not in page:
        return head, []
    body = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        cells = [cell(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if cells:
            rows.append(cells)
    return head, rows


def image_urls(page: str) -> list[str]:
    """A paper report's page images, in order."""
    urls = re.findall(r'<img[^>]+class="[^"]*filingImage[^"]*"[^>]+src="([^"]+)"', page)
    urls += [u for u in re.findall(r'<img[^>]+src="([^"]+)"[^>]+class="[^"]*filingImage[^"]*"', page) if u not in urls]
    urls = urls or [u for u in re.findall(r'<img[^>]+src="([^"]+)"', page) if "/media/" in u and u.lower().endswith(".gif")]
    return [ROOT + u if u.startswith("/") else u for u in urls]


def page_of(c: Client, rep: dict) -> str:
    """A report's page. A session that has lapsed answers with the notice instead: accept it once and ask again."""
    page = c.call("GET", rep["url"], headers={"Referer": SEARCH}).text
    if "prohibition_agreement" in page:
        c.accept_notice()
        page = c.call("GET", rep["url"], headers={"Referer": SEARCH}).text
        if "prohibition_agreement" in page:
            raise RuntimeError("the site answered with its notice, not the report")
    return page


def read_electronic(c: Client, rep: dict, out: pathlib.Path) -> dict:
    page = page_of(c, rep)
    head, rows = table_rows(page)
    (out / "reports").mkdir(parents=True, exist_ok=True)
    (out / "reports" / f"{rep['id']}.html").write_text(page, encoding="utf-8")
    return {**rep, "columns": head, "rows": rows}


def read_paper(c: Client, rep: dict, out: pathlib.Path) -> dict:
    page = page_of(c, rep)
    urls = image_urls(page)
    folder = out / "paper" / rep["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "page.html").write_text(page, encoding="utf-8")
    if not urls:
        raise RuntimeError("no page images found on the report's page: see its page.html")
    files = []
    for n, u in enumerate(urls, 1):
        b = c.call("GET", u, headers={"Referer": rep["url"]}).content
        name = "p{:04d}{}".format(n, pathlib.PurePosixPath(u).suffix.lower() or ".gif")
        (folder / name).write_bytes(b)
        files.append({"file": f"data/paper/{rep['id']}/{name}", "source": u, "bytes": len(b)})
    return {**rep, "pages": files}


def saved(out: pathlib.Path) -> set[str]:
    return {p.stem for p in (out / "reports").glob("*.json")} if (out / "reports").exists() else set()


def run(c: Client, out: pathlib.Path, now, *, since: dt.date = START) -> dict:
    t0, stats = c.clock(), {"read": 0, "electronic": 0, "paper": 0, "other": 0, "failed": [], "stopped_early": False}
    c.accept_notice()
    listed = [report_of(r) for r in c.search(since)]
    out.mkdir(parents=True, exist_ok=True)
    # the list is the Senate's latest word: a report once listed and gone from it stays in index.json, marked
    old = json.loads((out / "index.json").read_text(encoding="utf-8")) if (out / "index.json").exists() else {"reports": []}
    ids = {r["id"] for r in listed}
    gone = [{**r, "gone_from_search": r.get("gone_from_search") or now()} for r in old["reports"] if r["id"] not in ids]
    have = saved(out)
    for rep in listed:
        if rep["id"] in have:
            continue
        if c.clock() - t0 > MAX_RUN_S:
            stats["stopped_early"] = True
            break
        stats[rep["kind"]] = stats.get(rep["kind"], 0) + 1
        try:
            got = read_paper(c, rep, out) if rep["kind"] == "paper" else read_electronic(c, rep, out) \
                if rep["kind"] == "electronic" else dict(rep)
        except Refused:
            raise
        except Exception as e:                # one report's failure is named, and the next run tries it again
            stats["failed"].append({"id": rep["id"], "url": rep["url"], "error": f"{type(e).__name__}: {e}"[:300]})
            continue
        got["read_utc"] = now()
        (out / "reports").mkdir(parents=True, exist_ok=True)
        (out / "reports" / f"{rep['id']}.json").write_text(json.dumps(got, indent=1, sort_keys=True), encoding="utf-8")
        stats["read"] += 1
    listed.sort(key=lambda r: (r["filed"] or "", r["id"]), reverse=True)
    (out / "index.json").write_text(json.dumps({"from": since.isoformat(), "listed_utc": now(), "reports": listed + gone},
                                               indent=0, sort_keys=True), encoding="utf-8")
    return {**stats, "listed": len(listed), "gone_from_search": len(gone), "saved": len(saved(out)), "requests": c.requests}


def write_last(path: pathlib.Path, status: str, now: str, **fields) -> dict:
    before = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    rec = {"status": status, "finished_utc": now, "last_ok_utc": now if status == "ok" else before.get("last_ok_utc"), **fields}
    path.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return rec


def main() -> int:
    now = lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c = Client()
    try:
        stats = run(c, OUT, now)
    except Refused as e:
        print(write_last(LAST, "refused", now(), detail=str(e)[:600], requests=c.requests))
        return 1
    except Exception as e:
        print(write_last(LAST, "failed", now(), detail=f"{type(e).__name__}: {e}"[:600], requests=c.requests))
        return 1
    print(write_last(LAST, "ok" if not stats["failed"] else "partial", now(), **stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
