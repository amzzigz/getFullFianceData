from __future__ import annotations

import argparse
import json
import os
import time
import sys
import uuid
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from ziniu_auth_login_extracted import ZiniuAuthLogin


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="最小化测试紫鸟 WebDriver 启动和 getBrowserList 鉴权。")
    parser.add_argument("--ziniu-dir", default="", help="紫鸟安装目录，例如 D:\\紫鸟\\ziniao。")
    return parser.parse_args()


def browser_list_error(response: dict) -> str:
    status_code = str(response.get("statusCode"))
    status_message = str(response.get("statusMessage") or response.get("err") or "")
    if status_code == "-10000":
        return (
            "getBrowserList 返回未公开定义的错误码 -10000。请依次检查紫鸟客户端/WebDriver 是否正常、"
            "企业是否已认证并开通 WebDriver、company/username/password 是否正确，以及成员账号是否有浏览器环境权限；"
            "仍失败时请将该响应提交紫鸟官方支持确认"
        )
    if status_code == "-10003":
        return "getBrowserList 鉴权失败(-10003)：请检查 company/username/password"
    return f"getBrowserList failed: statusCode={status_code}, statusMessage={status_message}"


def update_core(client: ZiniuAuthLogin, attempts: int = 30) -> bool:
    print("preparing ZiNiao browser cores...")
    for attempt in range(1, attempts + 1):
        response = client.send_http(
            {
                "action": "updateCore",
                "requestId": f"update_core_{uuid.uuid4()}",
            }
        )
        if isinstance(response, dict):
            status_code = str(response.get("statusCode"))
            if status_code == "0":
                print("updateCore: OK")
                return True
            if status_code == "-10003":
                print("updateCore: unsupported by this ZiNiao version, continue getBrowserList test")
                return True
            print(f"updateCore attempt {attempt}: {json.dumps(response, ensure_ascii=False)}")
        else:
            print(f"updateCore attempt {attempt}: no response")
        time.sleep(2)
    return False


def main() -> int:
    args = parse_args()
    if args.ziniu_dir:
        os.environ["ZINIAO_INSTALL_DIR"] = str(Path(args.ziniu_dir).resolve())

    client = ZiniuAuthLogin()
    user_info = client.user_info or {}
    print(f"endpoint: {client.api_url}")
    print(f"ziniu dir: {os.environ.get('ZINIAO_INSTALL_DIR') or '(auto detect)'}")
    print(f"company: {user_info.get('company') or '(empty)'}")
    print(f"username: {user_info.get('username') or '(empty)'}")
    print(f"password configured: {'yes' if user_info.get('password') else 'no'}")

    probe = client.send_http({"action": "getBrowserList", "requestId": f"probe_{uuid.uuid4()}"})
    if not isinstance(probe, dict):
        online, online_error = client.ensure_client_online()
        if not online:
            print(f"result: FAILED - ZiNiao WebDriver unavailable: {online_error}")
            return 1
    else:
        print(f"initial endpoint response: {json.dumps({k: v for k, v in probe.items() if k != 'browserList'}, ensure_ascii=False)}")

    if not update_core(client):
        print("result: FAILED - updateCore did not complete")
        return 1

    response = client.send_http(
        {
            "action": "getBrowserList",
            "requestId": f"auth_test_{uuid.uuid4()}",
        }
    )
    if not isinstance(response, dict):
        print("result: FAILED - no JSON response from ZiNiao WebDriver")
        return 1

    safe_response = {
        key: value
        for key, value in response.items()
        if key not in {"browserList", "password"}
    }
    print(f"response: {json.dumps(safe_response, ensure_ascii=False)}")

    status_code = str(response.get("statusCode"))
    if status_code == "0":
        print(f"result: OK - browser count: {len(response.get('browserList') or [])}")
        return 0

    print(f"result: FAILED - {browser_list_error(response)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
