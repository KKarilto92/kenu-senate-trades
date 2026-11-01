"""Self-test of collect.py on a fake site. Run: python -B test_collect.py"""
import datetime as dt, json, pathlib, tempfile
import collect as C

HOME_PAGE = '<form><input name="csrfmiddlewaretoken" value="T1"><input name="prohibition_agreement"></form>'
SEARCH_JSON = {"recordsTotal": 3, "recordsFiltered": 3, "data": [
    ["Jane", "DOE", "Doe, Jane (Senator)", '<a href="/search/view/ptr/aaa-111/" target="_blank">Periodic Transaction Report for 01/15/2025</a>', "01/20/2025"],
    ["John", "ROE", "Roe, John (Senator)", '<a href="/search/view/paper/bbb-222/" target="_blank">Periodic Transaction Report (paper)</a>', "02/03/2025"],
    ["Ann", "POE", "Poe, Ann (Senator)", '<a href="/search/view/ptr/ccc-333/" target="_blank">Periodic Transaction Report for 03/01/2025 (Amendment 1)</a>', "03/02/2025"]]}
PTR = """<table><thead><tr><th>#</th><th>Transaction Date</th><th>Owner</th><th>Ticker</th><th>Asset Name</th><th>Asset Type</th><th>Type</th><th>Amount</th><th>Comment</th></tr></thead>
<tbody><tr><td>1</td><td>01/10/2025</td><td>Self</td><td><a href="https://finance.yahoo.com/q?s=AAPL">AAPL</a></td><td>Apple Inc</td><td>Stock</td><td>Purchase</td><td>$1,001 - $15,000</td><td>--</td></tr></tbody></table>"""
PAPER = '<img class="filingImage" src="https://efd-media-public.senate.gov/media/2025/1.gif"><img src="https://efd-media-public.senate.gov/media/2025/2.gif" class="filingImage x">'

class R:
    def __init__(s, text="", status=200, url="", content=b""):
        s.text, s.status_code, s.url, s.content = text, status, url, content
    def json(s): return json.loads(s.text)
    def raise_for_status(s):
        if s.status_code >= 400: raise RuntimeError(s.status_code)

class Site:
    def __init__(s, refuse=False):
        s.headers, s.cookies, s.refuse, s.log = {}, {"csrftoken": "T2"}, refuse, []
    def request(s, method, url, timeout=None, data=None, headers=None):
        s.log.append((method, url))
        if s.refuse: return R("<H1>Access Denied</H1>", 403, url)
        if url == C.HOME: return R(HOME_PAGE if method == "GET" else "ok", 200, C.SEARCH)
        if url == C.DATA: return R(json.dumps(SEARCH_JSON), 200, url)
        if "/ptr/" in url: return R(PTR, 200, url)
        if "/paper/" in url: return R(PAPER, 200, url)
        if url.endswith(".gif"): return R(status=200, url=url, content=b"GIF89a")
        raise AssertionError(url)

def client(site): return C.Client(session=site, pause=lambda s: None, clock=lambda: 0.0)
now = lambda: "2026-11-01T00:00:00+00:00"
with tempfile.TemporaryDirectory() as d:
    d = pathlib.Path(d); out = d / "data"; last = d / "last_run.json"
    s = C.run(client(Site()), out, now)
    assert (s["read"], s["electronic"], s["paper"], s["listed"], s["saved"]) == (3, 2, 1, 3, 3), s
    j = json.loads((out / "reports" / "aaa-111.json").read_text())
    assert j["columns"][1] == "Transaction Date" and j["rows"][0][3] == "AAPL" and j["filed"] == "2025-01-20" and j["kind"] == "electronic", j
    p = json.loads((out / "reports" / "bbb-222.json").read_text())
    assert [x["file"] for x in p["pages"]] == ["data/paper/bbb-222/p0001.gif", "data/paper/bbb-222/p0002.gif"], p
    assert (out / "paper" / "bbb-222" / "p0002.gif").read_bytes() == b"GIF89a"
    idx = json.loads((out / "index.json").read_text())
    assert [r["id"] for r in idx["reports"]] == ["ccc-333", "bbb-222", "aaa-111"], idx
    # a second run reads nothing again
    site = Site(); s2 = C.run(client(site), out, now)
    assert s2["read"] == 0 and not [u for m, u in site.log if "/view/" in u], s2
    # a refusal is recorded, keeps the last good time, and never reads on
    C.write_last(last, "ok", "2026-11-01T00:00:00+00:00", read=3)
    site = Site(refuse=True)
    try:
        C.run(client(site), out, now); raise AssertionError("no refusal")
    except C.Refused as e:
        rec = C.write_last(last, "refused", "2026-11-01T04:00:00+00:00", detail=str(e))
    assert rec["status"] == "refused" and rec["last_ok_utc"] == "2026-11-01T00:00:00+00:00" and "Access Denied" in rec["detail"], rec
    assert len(site.log) == 1, site.log
print("collector self-test ok")
