import importlib.util
import sys
from pathlib import Path


def load_auth_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "ziniu_auth_login_extracted.py"
    spec = importlib.util.spec_from_file_location("finance_ziniu_auth_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tiktok_pop_e1_e2_use_email_login():
    module = load_auth_module()

    assert module.ZiniuAuthLogin._tiktok_should_use_email_login("TIKTOK-POP-E1")
    assert module.ZiniuAuthLogin._tiktok_should_use_email_login("TIKTOK-POP-E2-SL")
    assert not module.ZiniuAuthLogin._tiktok_should_use_email_login("C1主账号")


def test_tiktok_email_login_clicks_switch_before_submit():
    module = load_auth_module()
    clicks = []

    class FakeElement:
        def __init__(self, name):
            self.name = name

        def click(self, by_js=False):
            clicks.append((self.name, by_js))

    class FakePage:
        def ele(self, selector, timeout=0):
            if "TikTok_Ads_SSO_Login_Email_Panel_Button" in selector:
                return FakeElement("email")
            if "Log in" in selector or "登录" in selector:
                return FakeElement("login")
            return None

        def run_js(self, script):
            return {
                "emailVisible": True,
                "emailValue": "liu983771@gmail.com",
                "passwordVisible": True,
                "passwordFilled": True,
                "loginDisabled": False,
            }

    module.ZiniuAuthLogin._handle_click_for_platform(
        FakePage(),
        "tiktok",
        "https://seller.tiktokshopglobalselling.com/account/login",
        lambda msg: None,
        None,
        account="TIKTOK-POP-E1",
    )

    assert clicks == [("email", False), ("login", True)]


def test_tiktok_email_login_does_not_submit_when_login_disabled():
    module = load_auth_module()
    clicks = []

    class FakeElement:
        def __init__(self, name):
            self.name = name

        def click(self, by_js=False):
            clicks.append((self.name, by_js))

    class FakePage:
        def ele(self, selector, timeout=0):
            if "TikTok_Ads_SSO_Login_Email_Panel_Button" in selector:
                return FakeElement("email")
            if "Log in" in selector or "登录" in selector:
                return FakeElement("login")
            return None

        def run_js(self, script):
            return {
                "emailPanelActive": True,
                "emailSwitchVisible": False,
                "emailVisible": True,
                "emailValue": "liu983771@gmail.com",
                "mobileVisible": False,
                "passwordVisible": True,
                "passwordFilled": False,
                "loginDisabled": True,
            }

    module.ZiniuAuthLogin._handle_click_for_platform(
        FakePage(),
        "tiktok",
        "https://seller.tiktokshopglobalselling.com/account/login",
        lambda msg: None,
        None,
        account="TIKTOK-POP-E1",
    )

    assert clicks == [("email", False)]


def test_tiktok_email_login_does_not_submit_until_email_panel_is_active(monkeypatch):
    module = load_auth_module()
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    clicks = []

    class FakeElement:
        def __init__(self, name):
            self.name = name

        def click(self, by_js=False):
            clicks.append((self.name, by_js))

    class FakePage:
        def ele(self, selector, timeout=0):
            if "TikTok_Ads_SSO_Login_Email_Panel_Button" in selector:
                return FakeElement("email")
            if "Log in" in selector or "登录" in selector:
                return FakeElement("login")
            return None

        def run_js(self, script):
            return {
                "emailPanelActive": False,
                "emailSwitchVisible": True,
                "emailVisible": True,
                "emailValue": "liu983771@gmail.com",
                "mobileVisible": True,
                "passwordVisible": True,
                "passwordFilled": True,
                "loginDisabled": False,
            }

    module.ZiniuAuthLogin._handle_click_for_platform(
        FakePage(),
        "tiktok",
        "https://seller.tiktokshopglobalselling.com/account/login",
        lambda msg: None,
        None,
        account="TIKTOK-POP-E1",
    )

    assert clicks == [("email", False)]


def test_tiktok_email_login_submits_when_email_mode_has_value_and_password(monkeypatch):
    module = load_auth_module()
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    clicks = []

    class FakeElement:
        def __init__(self, name):
            self.name = name

        def click(self, by_js=False):
            clicks.append((self.name, by_js))

    class FakePage:
        def ele(self, selector, timeout=0):
            if "TikTok_Ads_SSO_Login_Email_Panel_Button" in selector:
                return FakeElement("email")
            if "Log in" in selector or "登录" in selector:
                return FakeElement("login")
            return None

        def run_js(self, script):
            return {
                "emailPanelActive": False,
                "emailSwitchVisible": False,
                "emailVisible": False,
                "emailValue": "liu983771@gmail.com",
                "mobileVisible": False,
                "passwordVisible": True,
                "passwordFilled": True,
                "loginDisabled": False,
            }

    module.ZiniuAuthLogin._handle_click_for_platform(
        FakePage(),
        "tiktok",
        "https://seller.tiktokshopglobalselling.com/account/login",
        lambda msg: None,
        None,
        account="TIKTOK-POP-E1",
    )

    assert clicks == [("email", False), ("login", True)]


def test_tiktok_email_login_submits_when_email_mode_ready_even_if_password_value_hidden(monkeypatch):
    module = load_auth_module()
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    clicks = []

    class FakeElement:
        def __init__(self, name):
            self.name = name

        def click(self, by_js=False):
            clicks.append((self.name, by_js))

    class FakePage:
        def ele(self, selector, timeout=0):
            if "TikTok_Ads_SSO_Login_Email_Panel_Button" in selector:
                return FakeElement("email")
            if "Log in" in selector or "登录" in selector:
                return FakeElement("login")
            return None

        def run_js(self, script):
            return {
                "emailPanelActive": True,
                "emailSwitchVisible": False,
                "emailVisible": False,
                "emailValue": "liu983771@gmail.com",
                "mobileVisible": False,
                "passwordVisible": True,
                "passwordFilled": False,
                "loginDisabled": False,
            }

    module.ZiniuAuthLogin._handle_click_for_platform(
        FakePage(),
        "tiktok",
        "https://seller.tiktokshopglobalselling.com/account/login",
        lambda msg: None,
        None,
        account="TIKTOK-POP-E1",
    )

    assert clicks == [("email", False), ("login", True)]


def test_tiktok_email_login_supports_full_xpath_selector():
    module = load_auth_module()
    seen = []

    class FakeElement:
        def click(self, by_js=False):
            pass

    class FakePage:
        def ele(self, selector, timeout=0):
            seen.append(selector)
            if selector.startswith("xpath:/html/body/div[1]/section/section"):
                return FakeElement()
            return None

    assert module.ZiniuAuthLogin._click_tiktok_email_login(FakePage(), lambda msg: None)
    assert any(selector.startswith("xpath:/html/body/div[1]/section/section") for selector in seen)
