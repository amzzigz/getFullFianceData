from scripts.export_1688_hot_products import parse_rows, with_page


def test_with_page_replaces_current_page() -> None:
    url = "https://offer.1688.com/offer/manage_mini.vm?show_type=valid&currentPage=1&pageSize=20"

    assert with_page(url, 31) == "https://offer.1688.com/offer/manage_mini.vm?show_type=valid&currentPage=31&pageSize=20"


def test_parse_rows_only_returns_requested_columns() -> None:
    rows = parse_rows(
        {
            "items": [
                {
                    "itemNumber": "JFL100579",
                    "subject": "商品名",
                    "qualityStar": 6,
                    "offerId": 123,
                }
            ]
        }
    )

    assert rows == [("JFL100579", "商品名", 6)]
