from finance_crawler.platforms import merchant_billing, shein_funds


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class SequenceSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def post(self, _url, timeout):
        self.calls += 1
        return DummyResponse(self.payloads.pop(0))


def test_resolve_supplier_context_waits_through_subsystem_redirect(monkeypatch):
    session = SequenceSession(
        [
            {"code": "20302", "msg": "子系统登录重定向"},
            {"code": "0", "msg": "OK", "info": {"supplierId": 123}},
        ]
    )

    assert merchant_billing.resolve_supplier_context(session, 60) == {"supplierId": 123}
    assert session.calls == 2


def test_resolve_supplier_id_waits_through_subsystem_redirect(monkeypatch):
    session = SequenceSession(
        [
            {"code": "20302", "msg": "子系统登录重定向"},
            {"code": "0", "msg": "OK", "info": {"supplierId": 456}},
        ]
    )

    assert shein_funds.resolve_supplier_id(session, 60) == 456
    assert session.calls == 2
