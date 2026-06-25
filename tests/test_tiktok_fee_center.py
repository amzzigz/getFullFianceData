from datetime import datetime
from zoneinfo import ZoneInfo

from finance_crawler.periods import PeriodRange
from finance_crawler.platforms.tiktok_fee_center import (
    FEE_EXPORTS,
    build_fee_export_payload,
    record_matches_fee,
)


def _period() -> PeriodRange:
    tz = ZoneInfo("Asia/Shanghai")
    return PeriodRange(
        period_type="monthly",
        start=datetime(2026, 5, 1, 0, 0, 0, tzinfo=tz),
        end=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz),
    )


def test_fee_center_export_payloads_match_har_contract():
    period = _period()
    payloads = {item.id: build_fee_export_payload(item, period) for item in FEE_EXPORTS}

    assert payloads["logistics"]["task_type"] == 8
    assert payloads["logistics"]["download_params"]["list_invoice_items_request"] == {
        "param": {
            "invoice_date_begin": str(period.start_ms),
            "invoice_date_end": str(period.end_ms),
        },
        "query_source": "a_logistics_fee",
    }

    assert payloads["free_sample"]["query_param"] == {
        "bill_time_start": period.start_ms,
        "bill_time_end": period.end_ms,
        "bill_item_status": 3,
    }

    assert payloads["epr_pob"]["query_param"] == {
        "bill_period_begin_time_start": period.start_ms,
        "bill_period_begin_time_end": period.end_ms,
        "bill_status_list": [100],
    }


def test_fee_center_download_record_matching_is_tight():
    started_ms = 1780387900000
    logistics = FEE_EXPORTS[0]
    row = {
        "task_id": "7646693261248005909",
        "file_name": "物流供应链服务费账单明细_1780387981.xlsx",
        "source_name": "揽收、退货服务费账单明细",
        "status": 3,
        "download_time": 1780387981000,
    }
    assert record_matches_fee(row, logistics, "7646693261248005909", started_ms)
    assert record_matches_fee(row, logistics, "different-task", started_ms)
    assert not record_matches_fee({**row, "file_name": "已出账货款_订单明细.xlsx"}, logistics, "different-task", started_ms)
    assert not record_matches_fee({**row, "download_time": started_ms - 1}, logistics, "different-task", started_ms)
