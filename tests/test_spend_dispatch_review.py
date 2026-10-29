"""Independent regression for closing queued work before exceptional SSE settlement."""
import asyncio
import threading
from types import SimpleNamespace as NS

import pytest

from receipts import api, extract, stream
from test_api import parse_sse
from test_spend_usage import META, TOKENS, response


def test_serialization_failure_closes_queued_work_before_billing_and_refund(tmp_path):
    ledger = api.LocalLedger(1, tmp_path / "spend.sqlite3")
    budget = api.OperationBudget(ledger, ledger.reserve(.2), .2)
    stats = stream.CheckStats()
    release, ended = threading.Event(), threading.Event()
    provider_calls, ordering = [], []
    queued_usage = extract.RequestUsage(META, before_call=budget.before_call)

    def provider():
        provider_calls.append(True)
        return response(NS(**TOKENS))

    def queued():
        try:
            assert release.wait(2)
            try:
                queued_usage.call(provider, extract.build_request("Queued draft.", "", backend=META))
            except extract._RequestStopped:
                ordering.append("queued_canceled")
        finally:
            ended.set()

    worker = threading.Thread(target=queued, name="offline-queued-model")
    worker.start()

    def events():
        budget.before_call(.05)
        try:
            # A completed request has usable billing, but an unexpected downstream
            # value fails serialization before the generator's accounting cleanup.
            yield "claim", {"unsupported": object()}
        finally:
            queued_usage.finish()
            stats.cost_usd = .0123
            stats.model_calls = 1
            ordering.append("closed")
            release.set()

    source = events()

    def finish():
        ordering.append("settled")
        budget.finish(stats)

    async def collect():
        return "".join([part async for part in api._from_thread(source, finish, .01, stats, budget)])

    try:
        payloads = parse_sse(asyncio.run(collect()))
        assert ordering.index("closed") < ordering.index("settled")
        assert payloads[-1][0] == "error"
        assert payloads[-1][1]["cost_usd"] == pytest.approx(.0123)
        assert payloads[-1][1]["model_calls"] == 1
        assert payloads[-1][1]["cost_is_estimate"] is False
        assert ledger.spent_today() == pytest.approx(.0123)
        assert ledger.pending_count() == 0
    finally:
        source.close()
        release.set()
        assert ended.wait(2)
        worker.join(2)
    assert not provider_calls and "queued_canceled" in ordering
    with pytest.raises(extract.ModelCallRejected):
        budget.before_call(.05)
