"""scripts/remap_releases.py: named release files are mapped again offline, each with the paired report its own
report names, exhibits read from the cache or the exhibit archive."""
import gzip
import json

import scripts.remap_releases as remap


class Cache:
    def __init__(self, cached: dict[str, bytes]):
        self.cached, self.indexes = cached, []
        self.live_requests = 0

    def is_cached(self, url):
        return url in self.cached

    def get(self, url, *, max_age=None):
        if url not in self.cached:
            raise remap.EdgarError(f"offline mode and not cached: {url}")
        return self.cached[url]

    def filing_index(self, cik, accession):
        self.indexes.append((cik, accession))
        return []

    def ticker_to_cik(self, ticker):
        return 903, "Example Ltd."

    def submissions(self, cik, *, since=None, max_age=None):
        rows = [{"accessionNumber": a, "form": f, "filingDate": d, "reportDate": r, "items": "",
                 "acceptanceDateTime": f"{d}T20:00:00.000Z", "primaryDocument": "x.htm"}
                for a, f, d, r in (("0000000903-26-000009", "8-K", "2026-04-28", "2026-04-28"),
                                   ("0000000903-26-000011", "10-Q", "2026-05-01", "2026-03-31"),
                                   ("0000000903-26-000012", "10-Q/A", "2026-05-20", "2026-03-31"))]
        return {"name": "Example Ltd."}, rows


def test_a_body_the_cache_lacks_is_read_from_the_exhibit_archive(tmp_path):
    url, cached = "https://www.sec.gov/Archives/edgar/data/903/000000090326000009/ex99.htm", "https://x.invalid/a"
    (tmp_path / "index.json").write_text(json.dumps({url: {"key": "k1"}}))
    (tmp_path / "k1.gz").write_bytes(gzip.compress(b"<html>archived</html>"))
    client = remap.Archived(Cache({cached: b"cached"}), archive=tmp_path)
    assert client.get(url) == b"<html>archived</html>" and client.get(cached) == b"cached"
    assert client.live_requests == 0


def test_each_release_is_planned_with_the_paired_report_its_report_names(tmp_path, monkeypatch):
    folder = tmp_path / "EXLT"
    folder.mkdir()
    (folder / "0000000903-26-000009.report.json").write_text(json.dumps(
        {"accepted_at": "2026-04-28T20:00:00+00:00", "tenq": "0000000903-26-000011"}))
    monkeypatch.setattr(remap, "archived_companyfacts", lambda cik: {"facts": {}})
    edgar = Cache({})
    jobs, companyfacts, missing = remap.plan(edgar, ["EXLT/0000000903-26-000009", "EXLT/0000000903-26-000099"],
                                             effort="minimal", pr_dir=tmp_path)
    assert missing == ["EXLT/0000000903-26-000099: no report"]
    assert [(j.eightk["accessionNumber"], j.tenq["accessionNumber"]) for j in jobs] == [
        ("0000000903-26-000009", "0000000903-26-000011")]  # not the later amendment
    assert edgar.indexes == [(903, "0000000903-26-000009")] and list(companyfacts) == [903]
