"""
Reusable ZiNiao auth/login helper extracted from module2.py.

Public API:
- auth_login(account: str) -> dict
- class ZiniuAuthLogin(...).auth_login(account: str) -> dict

Only one business parameter is required: account name.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

import psutil
import requests


try:
    import xbot  # type: ignore
except Exception:
    class _XbotMock:
        @staticmethod
        def print(msg: str) -> None:
            print(msg)

    xbot = _XbotMock()


API_URL = "http://127.0.0.1:16851"
DEFAULT_PORT = 16851

ZINIAO_AUTH_ENV_KEYS = {
    "company": "ZINIAO_COMPANY",
    "username": "ZINIAO_USERNAME",
    "password": "ZINIAO_PASSWORD",
}

COOKIE_MIN_LEN = 50
SHEIN_REQUIRED_COOKIE_KEYS = ["sso", "token", "session", "sid", "auth"]
COOKIE_DOMAIN_HINTS = {
    "shein": "geiwohuo.com",
    "temu_business": "kuajingmaihuo.com",
    "tiktok": "tiktokshopglobalselling.com",
    "aliexpress": "aliexpress.com",
}

PLATFORM_URLS = {
    "shein": "https://sso.geiwohuo.com/#/home/",
    "temu_business": "https://agentseller.temu.com/main/authentication?redirectUrl=https%3A%2F%2Fagentseller.temu.com%2Fmain%2Fauthentication%3FredirectUrl%3Dhttps%253A%252F%252Fagentseller.temu.com%252F",
    "tiktok": "https://seller.tiktokshopglobalselling.com/",
    "aliexpress": "https://csp.aliexpress.com/m_apps/funds-manage/financial_aechoice?channelId=2208665",
}

MAX_START_RETRY = 2
COOKIE_RETRY = 3
AUTO_MONITOR_SECONDS = 25
MANUAL_MONITOR_SECONDS = 15
MANUAL_CHECK_INTERVAL = 5

# Keep cleanup scope to V6 only, do not touch V5 SuperBrowser process.
TARGET_PROCESS_NAMES = ["ziniao.exe", "ziniaorenderer.exe"]
CLIENT_EXE_NAMES = ["ziniao.exe", "starter.exe", "Ziniao.exe"]

INSTALL_FOLDER_CANDIDATES = [
    r"F:\紫鸟\ziniao",
    r"F:\ziniao",
    r"F:\Program Files\ZiNiao",
    r"E:\紫鸟\ziniao",
    r"E:\ziniao",
    r"E:\Program Files\ZiNiao",
    r"D:\紫鸟\ziniao",
    r"D:\ziniao",
    r"C:\紫鸟\ziniao",
    r"C:\ziniao",
    r"C:\Program Files\ziniao",
    r"C:\Program Files\ZiNiao",
    r"C:\Program Files (x86)\ziniao",
    r"C:\Program Files (x86)\ZiNiao",
]


@dataclass
class AuthResult:
    success: bool
    message: str
    account: str
    platform: str = ""
    cookie: str = ""
    user_agent: str = ""
    final_url: str = ""
    browser_oauth: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_user_info_from_json(path: str, fallback: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            xbot.print(f"[auth] failed to read user info from {path}: {e}")
    return fallback or {}


def load_default_user_info() -> Dict[str, str]:
    user_info = {
        key: os.environ.get(env_key, "")
        for key, env_key in ZINIAO_AUTH_ENV_KEYS.items()
    }
    if all(user_info.values()):
        return user_info

    env_name = os.environ.get("FINANCE_CRAWLER_ENV", "prod")
    config_dir = os.environ.get("FINANCE_CRAWLER_CONFIG_DIR")
    secrets_path = (
        os.path.join(config_dir, f"secrets.{env_name}.json")
        if config_dir
        else os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", f"secrets.{env_name}.json")
    )
    try:
        data = json.loads(open(secrets_path, "r", encoding="utf-8").read())
        ziniu = data.get("ziniu") or {}
        for key in ZINIAO_AUTH_ENV_KEYS:
            if not user_info.get(key):
                user_info[key] = str(ziniu.get(key) or "")
    except Exception:
        pass
    return user_info


def _norm_platform(value: str) -> str:
    key = (value or "").strip().lower()
    if key in ("temu", "temu_business"):
        return "temu_business"
    return key


def _filter_cookies_for_shop(cookie_list: List[Dict[str, Any]], shop_lower: str) -> List[Dict[str, Any]]:
    cookies = cookie_list or []
    domain_hint = COOKIE_DOMAIN_HINTS.get(shop_lower)
    if domain_hint:
        filtered = [c for c in cookies if domain_hint in str(c.get("domain", ""))]
        if filtered:
            return filtered
    return cookies


def _cookie_str_from_list(cookie_list: List[Dict[str, Any]]) -> str:
    return "; ".join([f"{c.get('name')}={c.get('value')}" for c in cookie_list if c.get("name")])


def _has_required_cookie_keys(cookie_list: List[Dict[str, Any]], required_keys: List[str]) -> bool:
    if not required_keys:
        return True
    names = [str(c.get("name", "")).lower() for c in cookie_list]
    return any(any(key in name for key in required_keys) for name in names)


def _is_cookie_valid(shop_lower: str, cookie_list: List[Dict[str, Any]]) -> Tuple[bool, str, List[Dict[str, Any]]]:
    filtered = _filter_cookies_for_shop(cookie_list, shop_lower)
    cookie_str = _cookie_str_from_list(filtered)
    if len(cookie_str) < COOKIE_MIN_LEN:
        return False, cookie_str, filtered
    if shop_lower == "shein" and not _has_required_cookie_keys(filtered, SHEIN_REQUIRED_COOKIE_KEYS):
        return False, cookie_str, filtered
    return True, cookie_str, filtered


def _safe_page_url(page: Any, browser: Any = None) -> str:
    try:
        return page.url
    except Exception:
        if browser:
            try:
                return browser.latest_tab.url
            except Exception:
                return ""
        return ""


def _is_noise_page_url(url: str) -> bool:
    lowered = (url or "").lower()
    return (
        not lowered
        or lowered.startswith("chrome-extension://")
        or lowered.startswith("devtools://")
        or "about:blank" in lowered
        or "data:," in lowered
    )


def _pick_shop_page(page: Any, browser: Any = None, target_url: str = "") -> Any:
    current_url = _safe_page_url(page, browser).lower()
    target_host = target_url.split("/")[2].lower() if target_url and "://" in target_url else ""
    if not _is_noise_page_url(current_url):
        if not target_host or target_host in current_url:
            return page
    if browser:
        try:
            latest_page = browser.latest_tab
            latest_url = _safe_page_url(latest_page, browser).lower()
            if not _is_noise_page_url(latest_url):
                if not target_host or target_host in latest_url:
                    return latest_page
        except Exception:
            pass
    return page


class ZiniuAuthLogin:
    def __init__(
        self,
        user_info: Optional[Dict[str, str]] = None,
        api_url: Optional[str] = None,
        request_timeout: int = 10,
        logger: Any = xbot,
    ) -> None:
        self.user_info = user_info if user_info is not None else load_default_user_info()
        self.api_url = api_url or os.environ.get("ZINIAO_API_URL") or API_URL
        self.webdriver_port = self._resolve_webdriver_port()
        self.request_timeout = request_timeout
        self.logger = logger

    def _resolve_webdriver_port(self) -> int:
        raw_port = os.environ.get("ZINIAO_WEBDRIVER_PORT")
        if raw_port:
            try:
                return int(raw_port)
            except (TypeError, ValueError):
                pass
        match = re.search(r":(\d+)(?:/)?$", self.api_url)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                pass
        return DEFAULT_PORT

    def _log(self, msg: str) -> None:
        try:
            self.logger.print(msg)
        except Exception:
            print(msg)

    @staticmethod
    def build_start_browser_payload(info: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "action": "startBrowser",
            "requestId": str(uuid.uuid4()),
            "openType": "1",
            "runMode": "1",
            "isWebDriverReadOnlyMode": 0,
        }
        browser_id = info.get("browserId") or info.get("id")
        browser_oauth = info.get("browserOauth")
        if browser_id:
            payload["browserId"] = str(browser_id)
        if browser_oauth:
            payload["browserOauth"] = browser_oauth
        return payload

    @staticmethod
    def _build_launch_env() -> Dict[str, str]:
        # In some terminal environments this variable is set globally,
        # which makes Electron app run as plain Node and breaks startup args.
        env = os.environ.copy()
        env.pop("ELECTRON_RUN_AS_NODE", None)
        return env

    def send_http(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            full_payload = {**data, **(self.user_info or {})}
            json_str = json.dumps(full_payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            response = requests.post(
                self.api_url,
                data=json_str,
                headers=headers,
                timeout=self.request_timeout,
            )
            return response.json()
        except Exception as e:
            self._log(f"[auth] send_http failed: {e}")
            return None

    @staticmethod
    def _browser_list_is_empty(response: Optional[Dict[str, Any]]) -> bool:
        return bool(
            response
            and str(response.get("statusCode")) == "0"
            and isinstance(response.get("browserList"), list)
            and len(response.get("browserList") or []) == 0
        )

    @staticmethod
    def _browser_list_error(response: Optional[Dict[str, Any]], require_non_empty: bool = False) -> str:
        if not response:
            return "getBrowserList no response"
        status_code = str(response.get("statusCode"))
        status_message = str(response.get("statusMessage") or response.get("err") or "")
        if status_code == "0":
            if require_non_empty and ZiniuAuthLogin._browser_list_is_empty(response):
                return (
                    "getBrowserList browserList is empty: 紫鸟接口在线但浏览器环境列表为空，"
                    "通常是紫鸟客户端/成员态异常或当前账号无环境权限"
                )
            return ""
        if status_code == "-10000":
            return (
                "getBrowserList 返回未公开定义的错误码 -10000：请检查紫鸟客户端/WebDriver、企业认证及 WebDriver 开通状态、"
                "company/username/password 和成员浏览器环境权限；仍失败时联系紫鸟官方支持确认"
            )
        if status_code == "-10003":
            return "getBrowserList 鉴权失败(-10003)：请检查 company/username/password"
        return f"getBrowserList failed: statusCode={status_code}, statusMessage={status_message}"

    def kill_all_processes(self) -> None:
        self._log("[auth] killing ZiNiao related processes...")
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                proc_name = str(proc.info.get("name") or "").lower()
                if any(target in proc_name for target in TARGET_PROCESS_NAMES):
                    p = psutil.Process(proc.info["pid"])
                    for child in p.children(recursive=True):
                        child.kill()
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue
        time.sleep(2)

    def _detect_install_folder_from_process(self) -> Optional[str]:
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                proc_name = str(proc.info.get("name") or "").lower()
                if any(name in proc_name for name in ["ziniao.exe", "starter.exe"]):
                    exe_path = proc.info.get("exe")
                    if exe_path and os.path.exists(exe_path):
                        return os.path.dirname(exe_path)
            except Exception:
                continue
        return None

    def _detect_install_folder(self) -> Optional[str]:
        process_path = self._detect_install_folder_from_process()
        if process_path:
            return process_path

        env_path = os.environ.get("ZINIAO_INSTALL_DIR")
        if env_path:
            for exe_name in CLIENT_EXE_NAMES:
                if os.path.exists(os.path.join(env_path, exe_name)):
                    return env_path

        for folder in INSTALL_FOLDER_CANDIDATES:
            for exe_name in CLIENT_EXE_NAMES:
                if os.path.exists(os.path.join(folder, exe_name)):
                    return folder
        return None

    def ensure_client_online(self) -> Tuple[bool, str]:
        self._log("[auth] checking webdriver endpoint...")
        test_res = self.send_http({"action": "getBrowserList", "requestId": "check_env"})
        error = self._browser_list_error(test_res)
        if not error:
            return True, ""
        if test_res and "statusCode" in test_res:
            return False, error

        install_folder = self._detect_install_folder()
        if not install_folder:
            return False, "ZiNiao install folder not found"

        self._log(f"[auth] endpoint down, restarting client from {install_folder}")
        self.kill_all_processes()

        exe_path = next(
            (
                os.path.join(install_folder, n)
                for n in CLIENT_EXE_NAMES
                if os.path.exists(os.path.join(install_folder, n))
            ),
            None,
        )
        if not exe_path:
            return False, f"ZiNiao executable not found under {install_folder}"

        cmd = [exe_path, "--run_type=web_driver", "--ipc_type=http", f"--port={self.webdriver_port}"]
        try:
            launch_env = self._build_launch_env()
            subprocess.Popen(cmd, cwd=install_folder, shell=False, env=launch_env)
        except Exception as e:
            return False, f"failed to start ZiNiao client: {e}"

        for _ in range(15):
            time.sleep(1)
            response = self.send_http({"action": "getBrowserList", "requestId": "wait_ready"})
            if not self._browser_list_error(response):
                return True, ""
        return False, f"webdriver endpoint still unavailable after restart: {error}"

    def get_shop_info(self, account: str) -> Tuple[Optional[Dict[str, Any]], str]:
        res = self.send_http({"action": "getBrowserList", "requestId": str(uuid.uuid4())})
        if not res:
            return None, "getBrowserList no response"

        status_code = str(res.get("statusCode"))
        if status_code == "0":
            browser_list = res.get("browserList", []) or []
            if not browser_list:
                return None, (
                    "getBrowserList browserList is empty: 紫鸟接口在线但浏览器环境列表为空，"
                    "请重启紫鸟或检查当前紫鸟成员环境权限"
                )
            normalized_account = self._normalize_browser_name(account)
            for shop in browser_list:
                browser_name = str(shop.get("browserName", ""))
                normalized_browser_name = self._normalize_browser_name(browser_name)
                if account in browser_name or (
                    normalized_account and normalized_account in normalized_browser_name
                ):
                    b_id = shop.get("browserId") or shop.get("id")
                    return {
                        "browserId": str(b_id) if b_id else None,
                        "browserOauth": shop.get("browserOauth"),
                        "name": shop.get("browserName", ""),
                        "raw": shop,
                    }, ""
            samples = ", ".join(str(item.get("browserName", "")) for item in browser_list[:8])
            return None, f"account not found in browserList: account={account}, count={len(browser_list)}, samples=[{samples}]"

        return None, self._browser_list_error(res)

    @staticmethod
    def _normalize_browser_name(value: str) -> str:
        return "".join(ch for ch in str(value or "").upper() if ch.isalnum())

    @staticmethod
    def _infer_platform_from_text(text: str) -> str:
        t = (text or "").lower()
        if any(k in t for k in ["shein", "geiwohuo"]) or re.search(r"\ba\d+pop\b", t):
            return "shein"
        if any(k in t for k in ["temu", "kuajingmaihuo"]):
            return "temu_business"
        if any(k in t for k in ["tiktok", "ttshop", "tiktokshopglobalselling"]):
            return "tiktok"
        if any(k in t for k in ["aliexpress", "速卖通"]):
            return "aliexpress"
        return ""

    def _infer_platform_from_shop(self, info: Dict[str, Any]) -> str:
        pieces: List[str] = []
        pieces.append(str(info.get("name", "")))
        raw = info.get("raw") or {}
        if isinstance(raw, dict):
            for v in raw.values():
                if isinstance(v, (str, int, float)):
                    pieces.append(str(v))
        return self._infer_platform_from_text(" ".join(pieces))

    @staticmethod
    def _infer_platform_from_url(url: str) -> str:
        u = (url or "").lower()
        if "geiwohuo.com" in u:
            return "shein"
        if "kuajingmaihuo.com" in u or "temu.com" in u:
            return "temu_business"
        if "tiktokshopglobalselling.com" in u:
            return "tiktok"
        if "aliexpress.com" in u:
            return "aliexpress"
        return ""

    @staticmethod
    def _is_login_success(platform: str, current_url: str, page: Any) -> bool:
        url = (current_url or "").lower()
        checks: List[Tuple[str, bool]]
        checks = [
            ("shein", "home" in url and "login" not in url),
            (
                "temu_business",
                (
                    ("seller.kuajingmaihuo.com" in url and "login" not in url)
                    or (
                        "agentseller.temu.com" in url
                        and "authentication" not in url
                        and "login" not in url
                    )
                ),
            ),
            ("tiktok", "homepage" in url and "login" not in url),
            (
                "aliexpress",
                (
                    "csp.aliexpress.com" in url
                    or "seller.aliexpress.com" in url
                    or "seller-acs.aliexpress.com" in url
                )
                and "login" not in url,
            ),
        ]

        if platform:
            checks = [x for x in checks if x[0] == platform]

        for _, ok in checks:
            if ok:
                try:
                    if page.ele("text=please reload this page", timeout=2):
                        continue
                except Exception:
                    pass
                return True
        return False

    @staticmethod
    def _tiktok_should_use_email_login(account: str) -> bool:
        text = str(account or "").upper()
        return bool(re.search(r"(?:^|[^A-Z0-9])E[12](?:[^A-Z0-9]|$)", text))

    @staticmethod
    def _click_tiktok_email_login(page: Any, log_fn: Any) -> bool:
        selectors = (
            "css:#TikTok_Ads_SSO_Login_Email_Panel_Button",
            "#TikTok_Ads_SSO_Login_Email_Panel_Button",
            "xpath:/html/body/div[1]/section/section/div/div/div[2]/div/div[1]/section/div[1]/div[4]/div[2]/span[2]",
            "xpath://html/body/div[1]/section/section/div/div/div[2]/div/div[1]/section/div[1]/div[4]/div[2]/span[2]",
            "/html/body/div[1]/section/section/div/div/div[2]/div/div[1]/section/div[1]/div[4]/div[2]/span[2]",
            'xpath://*[contains(normalize-space(.), "使用邮箱登录")]',
            'xpath://*[contains(normalize-space(.), "Email") and contains(normalize-space(.), "login")]',
            'xpath://*[@id="TikTok_Ads_SSO_Login_Email_Panel_Button"]',
        )
        for selector in selectors:
            try:
                btn = page.ele(selector, timeout=0.8)
                if btn:
                    log_fn("[auth] TikTok E1/E2 use email login")
                    try:
                        btn.click()
                    except Exception:
                        btn.click(by_js=True)
                    time.sleep(1)
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def _tiktok_email_login_ready(page: Any, timeout_seconds: int, log_fn: Any) -> bool:
        script = """
            return (() => {
                const visible = (el) => {
                    if (!el) return false;
                    let node = el;
                    while (node && node.nodeType === 1) {
                        const style = window.getComputedStyle(node);
                        if (
                            style.display === 'none' ||
                            style.visibility === 'hidden' ||
                            style.opacity === '0' ||
                            node.classList.contains('ac-hide') ||
                            node.getAttribute('aria-hidden') === 'true'
                        ) return false;
                        node = node.parentElement;
                    }
                    const rect = el.getBoundingClientRect();
                    return !!(rect.width || rect.height);
                };
                const email = document.querySelector('#TikTok_Ads_SSO_Login_Email_Input');
                const pwd = document.querySelector('#TikTok_Ads_SSO_Login_Pwd_Input');
                const login = document.querySelector('#TikTok_Ads_SSO_Login_Btn');
                const emailForm = document.querySelector('#TikTok_Ads_SSO_Login_Email_FormItem');
                const mobileForm = document.querySelector('#TikTok_Ads_SSO_Login_Mobile_FormItem');
                const emailSwitch = document.querySelector('#TikTok_Ads_SSO_Login_Email_Panel_Button');
                if (email && visible(email)) {
                    try { email.focus(); email.dispatchEvent(new Event('input', {bubbles: true})); } catch (e) {}
                }
                if (pwd && visible(pwd)) {
                    try { pwd.focus(); } catch (e) {}
                }
                const emailVisible = visible(email);
                const mobileVisible = visible(mobileForm);
                const emailValue = email ? email.value : '';
                const emailSwitchVisible = visible(emailSwitch);
                const emailPanelActive = (
                    (visible(emailForm) || emailVisible || (!emailSwitchVisible && emailValue.includes('@'))) &&
                    !mobileVisible
                );
                return {
                    emailPanelActive,
                    emailSwitchVisible,
                    emailVisible,
                    emailValue,
                    mobileVisible,
                    passwordVisible: visible(pwd),
                    passwordFilled: !!(pwd && pwd.value),
                    loginDisabled: !!(login && login.disabled)
                };
            })();
        """
        end_at = time.time() + max(1, timeout_seconds)
        last_state: Dict[str, Any] = {}
        while time.time() < end_at:
            try:
                state = page.run_js(script)
                if isinstance(state, dict):
                    last_state = state
                    email_value = str(state.get("emailValue") or "")
                    derived_email_panel_active = (
                        (state.get("emailVisible") or (not state.get("emailSwitchVisible") and "@" in email_value))
                        and not state.get("mobileVisible")
                    )
                    email_panel_active = bool(state.get("emailPanelActive")) or bool(derived_email_panel_active)
                    if (
                        email_panel_active
                        and "@" in email_value
                        and state.get("passwordVisible")
                        and not state.get("loginDisabled")
                    ):
                        return True
            except Exception:
                pass
            time.sleep(1)
        log_fn(f"[auth] TikTok E1/E2 email login not ready: {last_state}")
        return False

    @staticmethod
    def _tick_temu_agreement_checkboxes(page: Any, log_fn: Any) -> int:
        try:
            checkboxes = page.eles('css:input[type="checkbox"]') or []
        except Exception:
            checkboxes = []
        if not checkboxes:
            try:
                checkbox = page.ele('css:input[type="checkbox"]', timeout=0.9)
                checkboxes = [checkbox] if checkbox else []
            except Exception:
                checkboxes = []

        clicked = 0
        for checkbox in checkboxes:
            try:
                checked = bool(checkbox.states.is_checked)
            except Exception:
                checked = False
            if checked:
                continue
            try:
                checkbox.click()
            except Exception:
                checkbox.click(by_js=True)
            clicked += 1
            time.sleep(0.3)
        if clicked:
            log_fn(f"[auth] TEMU tick {clicked} agreement checkbox(es)")
        return clicked

    @staticmethod
    def _temu_login_form_state(page: Any) -> Dict[str, Any]:
        try:
            state = page.run_js(
                """
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const autofilled = (el) => {
                    if (!el) return false;
                    try { return el.matches(':-webkit-autofill'); } catch (e) { return false; }
                };
                const allInputs = Array.from(document.querySelectorAll('input'));
                const inputs = allInputs.filter(visible);
                const password = inputs.find((el) => (el.type || '').toLowerCase() === 'password');
                const agreements = allInputs.filter((el) => (el.type || '').toLowerCase() === 'checkbox');
                const phone = inputs.find((el) => {
                    const type = (el.type || '').toLowerCase();
                    if (['password', 'checkbox', 'hidden', 'submit', 'button'].includes(type)) return false;
                    const hint = `${el.name || ''} ${el.placeholder || ''} ${el.autocomplete || ''}`.toLowerCase();
                    return type === 'tel' || hint.includes('phone') || hint.includes('mobile') || hint.includes('手机');
                }) || inputs.find((el) => !['password', 'checkbox', 'hidden', 'submit', 'button'].includes((el.type || '').toLowerCase()));
                const submit = Array.from(document.querySelectorAll('button,input[type="submit"]')).find((el) => {
                    if (!visible(el)) return false;
                    const text = `${el.innerText || ''} ${el.value || ''}`.trim();
                    return text.includes('授权登录') || text === '登录';
                });
                const now = Date.now();
                return {
                    phoneVisible: visible(phone),
                    phoneValue: phone ? String(phone.value || '').trim() : '',
                    phoneAutofilled: autofilled(phone),
                    passwordVisible: visible(password),
                    passwordValue: password ? String(password.value || '') : '',
                    passwordAutofilled: autofilled(password),
                    agreementPresent: agreements.length > 0,
                    agreementChecked: agreements.length > 0 && agreements.every((el) => !!el.checked),
                    submitDisabled: !!(submit && submit.disabled),
                    switchedRecently: now - Number(window.__financeCrawlerTemuPhoneSwitchedAt || 0) < 10000,
                    submittedRecently: now - Number(window.__financeCrawlerTemuLoginSubmittedAt || 0) < 10000
                };
                """
            )
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _click_temu_login_form(page: Any, log_fn: Any) -> bool:
        state = ZiniuAuthLogin._temu_login_form_state(page)
        if state.get("submittedRecently"):
            return False

        if not state.get("passwordVisible") and not state.get("switchedRecently"):
            tab = page.ele("text=手机号登录", timeout=0.8) or page.ele("text=账号登录", timeout=0.8)
            if tab:
                try:
                    tab.hover()
                    time.sleep(0.2)
                    tab.click()
                    page.run_js("window.__financeCrawlerTemuPhoneSwitchedAt = Date.now(); return true;")
                    time.sleep(2)
                except Exception as exc:
                    log_fn(f"[auth] TEMU native phone tab click failed, wait for manual/autofill: {exc}")

        for _ in range(8):
            state = ZiniuAuthLogin._temu_login_form_state(page)
            if (
                state.get("phoneVisible")
                and (state.get("phoneValue") or state.get("phoneAutofilled"))
                and state.get("passwordVisible")
                and (state.get("passwordValue") or state.get("passwordAutofilled"))
                and not state.get("submitDisabled")
            ):
                break
            time.sleep(1)
        else:
            phone_ready = state.get("phoneVisible") and (state.get("phoneValue") or state.get("phoneAutofilled"))
            if not (phone_ready and state.get("passwordVisible") and not state.get("submitDisabled")):
                log_fn(f"[auth] TEMU login form waiting for saved credentials: {state}")
                return False
            log_fn("[auth] TEMU password appears protected; continue after autofill wait")

        checked = ZiniuAuthLogin._tick_temu_agreement_checkboxes(page, log_fn)
        if not checked:
            agreement = page.ele("text=我已阅读并同意", timeout=1)
            if agreement:
                try:
                    agreement.click()
                except Exception:
                    agreement.click(by_js=True)
                time.sleep(0.5)
            else:
                checkbox = page.ele('tag:input[type="checkbox"]', timeout=0.5)
                if checkbox:
                    checkbox.click(by_js=True)
                    time.sleep(0.5)

        agreement_state = ZiniuAuthLogin._temu_login_form_state(page)
        if agreement_state.get("agreementPresent") and not agreement_state.get("agreementChecked"):
            log_fn(f"[auth] TEMU agreement checkbox is still unchecked: {agreement_state}")
            return False

        btn = page.ele('xpath://button[contains(., "授权登录") or contains(., "登录")]', timeout=1)
        if not btn:
            btn = page.ele("@id=submit-button", timeout=0.8)
        if not btn:
            btn = page.ele('css:button[type="submit"]', timeout=0.8)
        if not btn:
            btn = page.ele("@data-testid=beast-core-button", timeout=1)
        if btn:
            log_fn("[auth] TEMU click login form submit")
            try:
                page.run_js("window.__financeCrawlerTemuLoginSubmittedAt = Date.now(); return true;")
            except Exception:
                pass
            try:
                btn.click()
            except Exception:
                btn.click(by_js=True)
            return True
        return False

    @staticmethod
    def _handle_click_for_platform(
        page: Any,
        platform: str,
        current_url: str,
        log_fn: Any,
        browser: Any = None,
        account: str = "",
    ) -> Any:
        url = (current_url or "").lower()
        detected = platform or ZiniuAuthLogin._infer_platform_from_url(url)

        if detected == "shein" and "home" not in url:
            btn = page.ele('css:button[type="submit"]', timeout=0.8) or page.ele("css:.soui-button-primary", timeout=0.8)
            if btn:
                btn.click(by_js=True)
            return page

        elif detected == "tiktok" and "login" in url:
            try:
                use_email_login = ZiniuAuthLogin._tiktok_should_use_email_login(account)
                if use_email_login:
                    if not ZiniuAuthLogin._click_tiktok_email_login(page, log_fn):
                        log_fn("[auth] TikTok E1/E2 email login switch not found; skip submit")
                        return page
                btn = page.ele('xpath://button[contains(., "Log in") or contains(., "登录")]', timeout=1)
                if not btn:
                    btn = page.ele("@data-testid=login-button", timeout=0.8)
                if not btn:
                    try:
                        shadow_host = page.ele("#root", timeout=0.5)
                        if shadow_host and shadow_host.shadow_root:
                            btn = shadow_host.shadow_root.ele("tag:button", timeout=0.5)
                    except Exception:
                        pass
                if btn:
                    if use_email_login:
                        if not ZiniuAuthLogin._tiktok_email_login_ready(page, 6, log_fn):
                            return page
                        log_fn("[auth] TikTok E1/E2 email login ready, click")
                        btn.click(by_js=True)
                        return page
                    log_fn("[auth] TikTok login button detected, click after short wait")
                    time.sleep(15)
                    btn.click(by_js=True)
            except Exception:
                pass
            return page

        elif detected == "aliexpress" and "login" in url:
            for selector in (
                'xpath://button[contains(normalize-space(.),"登录")]',
                'xpath://button[contains(normalize-space(.),"Sign in")]',
                'xpath://input[@type="submit"]',
                'xpath://button[@type="submit"]',
            ):
                try:
                    btn = page.ele(selector, timeout=1)
                    if btn:
                        log_fn("[auth] AliExpress login button detected, click")
                        try:
                            btn.click()
                        except Exception:
                            btn.click(by_js=True)
                        time.sleep(3)
                        break
                except Exception:
                    pass
            return page

        elif detected == "temu_business":
            try:
                if "seller.kuajingmaihuo.com" in url and "login" in url:
                    ZiniuAuthLogin._click_temu_login_form(page, log_fn)
                    return page

                def _scan_tabs() -> Tuple[List[Any], Any, Any, Any, Any]:
                    tabs_local: List[Any] = []
                    if browser:
                        try:
                            tabs_local = browser.get_tabs() or []
                        except Exception:
                            tabs_local = []
                    if not tabs_local:
                        tabs_local = [page]

                    auth_local = None
                    seller_local = None
                    login_local = None
                    success_local = None
                    for t in tabs_local:
                        t_url = _safe_page_url(t, browser).lower()
                        if "seller.kuajingmaihuo.com/settle/seller-login" in t_url:
                            login_local = t
                        if "seller.kuajingmaihuo.com/link-agent-seller" in t_url:
                            seller_local = t
                        if (
                            "agentseller.temu.com/main/authentication" in t_url
                            or "agentseller.temu.com/auth/authentication" in t_url
                        ):
                            auth_local = t
                        if "agentseller.temu.com" in t_url and "authentication" not in t_url and "login" not in t_url:
                            success_local = t
                    return tabs_local, auth_local, seller_local, login_local, success_local

                for _ in range(2):
                    _, auth_tab, seller_tab, login_tab, success_tab = _scan_tabs()
                    if login_tab:
                        ZiniuAuthLogin._click_temu_login_form(login_tab, log_fn)
                        return login_tab
                    if success_tab and not auth_tab and not seller_tab:
                        return success_tab

                    # Step 1: click card "商家中心" on agentseller auth page.
                    if not seller_tab:
                        work_tab = auth_tab or page
                        center = (
                            work_tab.ele(
                                'xpath://div[contains(@class,"authentication_goto") and contains(normalize-space(.),"商家中心")]',
                                timeout=1.1,
                            )
                            or work_tab.ele(
                                'xpath://div[contains(@class,"authentication_regionItem") and .//div[contains(@class,"authentication_goto")]]//div[contains(@class,"authentication_goto")]',
                                timeout=1.1,
                            )
                            or work_tab.ele('text=商家中心', timeout=0.9)
                        )
                        if center:
                            log_fn("[auth] TEMU step1 click 商家中心 card")
                            try:
                                center.click()
                            except Exception:
                                center.click(by_js=True)
                            time.sleep(1.1)

                    _, auth_tab, seller_tab, login_tab, success_tab = _scan_tabs()
                    if login_tab:
                        ZiniuAuthLogin._click_temu_login_form(login_tab, log_fn)
                        return login_tab
                    if success_tab and not auth_tab and not seller_tab:
                        return success_tab

                    # Step 2 + Step 3: in seller popup/tab, tick checkbox and confirm authorize.
                    popup_tab = seller_tab or auth_tab or page
                    authorization_clicked = False
                    auth_btn = (
                        popup_tab.ele('xpath://button[contains(., "确认授权并前往")]', timeout=1.1)
                        or popup_tab.ele("text=确认授权并前往", timeout=1.1)
                    )
                    if auth_btn:
                        ZiniuAuthLogin._tick_temu_agreement_checkboxes(popup_tab, log_fn)

                        log_fn("[auth] TEMU step3 click 确认授权并前往")
                        try:
                            auth_btn.click()
                        except Exception:
                            auth_btn.click(by_js=True)
                        authorization_clicked = True
                        time.sleep(1.2)

                    # Step 2b: if the current logged-in shop switch confirmation modal appears,
                    # click "确认切换" so the flow can continue without manual intervention.
                    switch_btn = (
                        popup_tab.ele('xpath://button[contains(., "确认切换")]', timeout=0.9)
                        or popup_tab.ele('text=确认切换', timeout=0.9)
                        or popup_tab.ele('xpath://button[contains(., "切换")]', timeout=0.7)
                    )
                    if switch_btn:
                        log_fn("[auth] TEMU step2b click 确认切换")
                        try:
                            switch_btn.click()
                        except Exception:
                            switch_btn.click(by_js=True)
                        time.sleep(1.2)
                    if authorization_clicked:
                        return popup_tab

                _, _, seller_tab, login_tab, success_tab = _scan_tabs()
                if login_tab:
                    ZiniuAuthLogin._click_temu_login_form(login_tab, log_fn)
                    return login_tab
                if seller_tab:
                    return seller_tab
                if success_tab:
                    return success_tab

                # Fallback: original login-form based flow.
                if "login" in url:
                    ZiniuAuthLogin._click_temu_login_form(page, log_fn)
            except Exception:
                pass
            return page

        return page

    def auth_login(self, account: str) -> Dict[str, Any]:
        account = (account or "").strip()
        if not account:
            return AuthResult(False, "account is empty", account="").to_dict()

        ok, err = self.ensure_client_online()
        if not ok:
            return AuthResult(False, f"client not ready: {err}", account=account).to_dict()

        info, info_err = self.get_shop_info(account)
        if not info:
            return AuthResult(False, info_err, account=account).to_dict()

        platform = self._infer_platform_from_shop(info)

        start_payload = self.build_start_browser_payload(info)

        res: Optional[Dict[str, Any]] = None
        for attempt in range(1, MAX_START_RETRY + 1):
            res = self.send_http(start_payload)
            if res and str(res.get("statusCode")) == "0":
                break
            self._log(f"[auth] startBrowser retry {attempt}/{MAX_START_RETRY}")
            time.sleep(2)
            if attempt == 1:
                self.ensure_client_online()

        if not res or str(res.get("statusCode")) != "0":
            msg = res.get("statusMessage") if isinstance(res, dict) else "no response"
            return AuthResult(False, f"startBrowser failed: {msg}", account=account, platform=platform).to_dict()

        port = res.get("debuggingPort")
        final_oauth = res.get("browserOauth") or info.get("browserOauth") or ""
        page = None
        browser = None
        final_url = ""

        try:
            from DrissionPage import Chromium, ChromiumOptions
        except Exception as e:
            self.send_http({"action": "stopBrowser", "requestId": str(uuid.uuid4()), "browserOauth": final_oauth})
            return AuthResult(False, f"DrissionPage import failed: {e}", account=account, platform=platform).to_dict()

        try:
            co = ChromiumOptions().set_local_port(port)
            browser = Chromium(co)
            page = browser.latest_tab

            target_url = PLATFORM_URLS.get(_norm_platform(platform), "")
            if target_url:
                page = _pick_shop_page(page, browser, target_url)
            else:
                page = _pick_shop_page(page, browser, "")

            current_page_url = _safe_page_url(page, browser)
            final_url = current_page_url
            if not platform:
                platform = self._infer_platform_from_url(current_page_url)
                target_url = PLATFORM_URLS.get(_norm_platform(platform), "")

            target_host = target_url.split("/")[2] if target_url else ""
            if target_url and (
                not current_page_url
                or "blank" in current_page_url
                or "data:," in current_page_url
                or (target_host and target_host not in current_page_url)
            ):
                try:
                    page.get(target_url)
                except Exception as e:
                    self._log(f"[auth] target url open failed: {e}")

            time.sleep(3)
            is_login_success = False

            for i in range(AUTO_MONITOR_SECONDS):
                try:
                    if i % 5 == 0:
                        page = _pick_shop_page(page, browser, target_url)
                    current_url = page.url.lower()
                except Exception:
                    time.sleep(1)
                    continue

                final_url = current_url
                if not platform:
                    platform = self._infer_platform_from_url(current_url)
                    target_url = PLATFORM_URLS.get(_norm_platform(platform), target_url)

                if self._is_login_success(platform, current_url, page):
                    is_login_success = True
                    break

                if i % 2 == 0:
                    try:
                        error_xpath = (
                            'xpath://*[contains(text(), "重新加载") or contains(text(), "ERR_") or '
                            'contains(text(), "无法访问") or contains(text(), "please reload")]'
                        )
                        try:
                            if page.ele(error_xpath, timeout=1):
                                page.refresh()
                                time.sleep(3)
                                continue
                        except Exception:
                            pass

                        page = self._handle_click_for_platform(page, platform, current_url, self._log, browser, account=account)

                    except Exception:
                        pass
                time.sleep(1)

            if not is_login_success:
                for _ in range(max(1, MANUAL_MONITOR_SECONDS // MANUAL_CHECK_INTERVAL)):
                    time.sleep(MANUAL_CHECK_INTERVAL)
                    try:
                        page = _pick_shop_page(page, browser, target_url)
                        current_url = page.url.lower()
                        final_url = current_url
                    except Exception:
                        continue
                    if not platform:
                        platform = self._infer_platform_from_url(current_url)
                    if self._is_login_success(platform, current_url, page):
                        is_login_success = True
                        break

            if not is_login_success:
                return AuthResult(
                    False,
                    "login timeout",
                    account=account,
                    platform=platform,
                    final_url=final_url,
                    browser_oauth=final_oauth,
                ).to_dict()

            time.sleep(2)
            try:
                user_agent = page.user_agent
            except Exception:
                user_agent = ""

            raw_cookies: List[Dict[str, Any]] = []
            cookie_ok = False
            cookie_str = ""
            filtered: List[Dict[str, Any]] = []
            shop_lower = _norm_platform(platform)

            for attempt in range(1, COOKIE_RETRY + 1):
                try:
                    raw_cookies = page.cookies()
                except Exception:
                    raw_cookies = []

                cookie_ok, cookie_str, filtered = _is_cookie_valid(shop_lower, raw_cookies)
                if not shop_lower:
                    cookie_str = _cookie_str_from_list(raw_cookies)
                    cookie_ok = len(cookie_str) >= COOKIE_MIN_LEN
                    filtered = raw_cookies

                if cookie_ok:
                    break

                if attempt < COOKIE_RETRY:
                    time.sleep(2)
                    try:
                        page = browser.latest_tab
                    except Exception:
                        pass

            if not cookie_ok:
                names_preview = [c.get("name", "") for c in filtered][:8]
                return AuthResult(
                    False,
                    f"cookie invalid, sample={names_preview}",
                    account=account,
                    platform=platform,
                    final_url=final_url,
                    browser_oauth=final_oauth,
                ).to_dict()

            return AuthResult(
                True,
                "success",
                account=account,
                platform=platform,
                cookie=cookie_str,
                user_agent=user_agent,
                final_url=final_url,
                browser_oauth=final_oauth,
            ).to_dict()
        except Exception as e:
            return AuthResult(
                False,
                f"runtime exception: {e}",
                account=account,
                platform=platform,
                final_url=final_url,
                browser_oauth=final_oauth,
            ).to_dict()
        finally:
            try:
                self.send_http({"action": "stopBrowser", "requestId": str(uuid.uuid4()), "browserOauth": final_oauth})
            except Exception:
                pass
            try:
                if page:
                    page.quit()
            except Exception:
                pass


_default_client = ZiniuAuthLogin()


def auth_login(account: str) -> Dict[str, Any]:
    """One-parameter entrypoint for other projects."""
    return _default_client.auth_login(account)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if len(sys.argv) < 2:
        print("Usage: python ziniu_auth_login_extracted.py <account_name>")
        raise SystemExit(1)

    account_name = sys.argv[1]
    result = auth_login(account_name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
