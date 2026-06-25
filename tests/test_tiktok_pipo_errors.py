from finance_crawler.platforms.tiktok_withdrawals import (
    cashier_bootstrap_ready_from_urls,
    extract_open_wallet_url,
    is_pipo_parameter_error,
    pipo_auth_headers_from_url,
)


def test_pipo_parameter_error_detects_sy0007_response():
    payload = {
        "response": '{"error_code":"sy0007","error_message":"parameter error","result_code":"error"}',
        "_inner_response": {
            "error_code": "sy0007",
            "error_message": "parameter error",
            "result_code": "error",
        },
    }

    assert is_pipo_parameter_error(payload)


def test_pipo_parameter_error_ignores_login_expired():
    payload = {
        "_inner_response": {
            "error_code": "LOGIN_STATUS_EXPIRED",
            "error_message": "Login status expired",
            "result_code": "error",
        },
    }

    assert not is_pipo_parameter_error(payload)


def test_extract_open_wallet_url_reads_seller_wallet_response():
    data = {
        "base_resp": {"message": "success", "code": 0},
        "data": {
            "open_wallet_url": (
                "https://cashier-my4a.pipopay.com/pipo/fe/business_wallet/wallet/views/main"
                "?wuid=4010000000045108449&merchant_id=11202309J9kyc1"
            )
        },
    }

    assert extract_open_wallet_url(data).endswith("merchant_id=11202309J9kyc1")


def test_cashier_bootstrap_ready_requires_wallet_bootstrap_calls():
    urls = [
        "https://cashier-my4a.pipopay.com/cashier/v1/user/info",
        "https://cashier-my4a.pipopay.com/wallet/v1/get_wallet_index",
    ]

    assert cashier_bootstrap_ready_from_urls(urls)


def test_cashier_bootstrap_ready_rejects_plain_page_load():
    urls = [
        "https://cashier-my4a.pipopay.com/pipo/fe/business_wallet/wallet/views/main",
    ]

    assert not cashier_bootstrap_ready_from_urls(urls)


def test_pipo_auth_headers_from_url_extracts_session_and_token():
    url = (
        "https://cashier-my4a.pipopay.com/pipo/fe/business_wallet/wallet/views/main"
        "?wuid=1&fp_session_id=session-1&fp_token=token-1&merchant_id=m1"
    )

    assert pipo_auth_headers_from_url(url) == {
        "pipo-fp-session-id": "session-1",
        "pipo-fp-token": "token-1",
    }
