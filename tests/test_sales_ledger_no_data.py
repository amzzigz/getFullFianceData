from finance_crawler.platforms.sales_ledger import is_sales_ledger_no_data_download_error


def test_sales_ledger_download_center_failed_file_is_no_data():
    message = (
        "下载中心轮询后仍未拿到文件链接: keywords=['台账变动明细'], extension=zip, "
        "last_file_response={'candidate': {'fileName': 'MILS-台账变动明细', "
        "'fileStatus': 2, 'failReason': 'MILS-导出文件失败'}}"
    )

    assert is_sales_ledger_no_data_download_error(message)


def test_sales_ledger_download_center_timeout_without_failed_file_is_not_no_data():
    message = "下载中心轮询后仍未拿到文件链接: keywords=['台账变动明细'], extension=zip"

    assert not is_sales_ledger_no_data_download_error(message)
