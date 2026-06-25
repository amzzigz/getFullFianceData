# SHEIN Tab Reconnect Design

## Goal

Use DrissionPage 4.1.1.4 native tab recovery to reduce transient SHEIN page disconnections without changing the existing account batching, cookie reuse, module concurrency, or ZiNiao serialization rules.

## Scope

The change covers both SHEIN browser-owning paths:

- Normal SHEIN/POP/A1B shared login and subsystem warm-up in `src/finance_crawler/auth.py`.
- A1Y-A4Y Shenhe report-bill startup and browser fetch operations in `src/finance_crawler/platforms/shenhe_report_bill.py`.

TikTok, TEMU, AliExpress, and the generic desktop helper login loop remain unchanged.

## Recovery Behavior

Both paths attach to the ZiNiao debugging port with:

```python
ChromiumOptions().set_local_port(debug_port).existing_only()
```

When DrissionPage reports `PageDisconnectedError`, `TargetNotFoundError`, or the existing localized disconnected message:

1. Call `page.reconnect(wait=1)` when the original tab object still supports reconnect.
2. Verify the reconnected tab by reading its URL.
3. If the target was destroyed, enumerate existing tabs for the expected host.
4. Fall back to `browser.latest_tab` only when it belongs to the expected host or is a blank page that can safely navigate to the target.
5. Retry tab recovery at most three times per browser session.
6. If recovery fails, preserve the existing cleanup and outer retry behavior; do not silently start another ordinary Chrome process.

Expected hosts:

- Normal SHEIN/POP/A1B: `geiwohuo.com`.
- A1Y-A4Y report bills: `shenhe888.com`.

## Normal SHEIN Flow

`_shein_shared_cookie_login_unlocked()` continues to start one ZiNiao browser, serially warm subsystem target URLs, and extract one account-level cookie.

The only behavioral change is that a transient disconnection inside navigation, login clicking, URL inspection, cookie collection, or user-agent collection first attempts tab recovery. After recovery, the current target is requested again when necessary. Exhausted recovery returns the existing `shein page disconnected during login` failure so account-level retry and module fallback remain intact.

## Shenhe Flow

`start_logged_in_page()` uses native attach and tab recovery while waiting for the Shenhe page.

After startup, browser-backed API calls use a small mutable page holder so a successful reconnect or replacement-tab selection is reused by subsequent list/export requests. A browser fetch retries only connection errors through this holder; ordinary API and JavaScript errors retain their current behavior.

The existing end-to-end `ziniu_auth_slot()` scope and `close_browser()` cleanup are unchanged.

## Tests

Add regression tests proving:

- Normal SHEIN startup uses `existing_only()`.
- Normal SHEIN reconnects the same tab and continues warm-up.
- Normal SHEIN selects a replacement `geiwohuo.com` tab when the old target disappears.
- Shenhe startup uses `existing_only()` and reconnects before failing.
- Shenhe browser fetch updates and reuses a replacement `shenhe888.com` tab.
- Recovery stops after three attempts and existing cleanup/failure behavior remains.

Run the focused SHEIN tests first, then the complete pytest suite.

## Success Criteria

- Unit tests demonstrate native reconnect and replacement-tab recovery in both SHEIN paths.
- Existing SHEIN batching, locking, cookie caching, and cleanup tests remain green.
- No non-SHEIN platform code changes.
- The result is delivered as an isolated test build before public snapshot publication.
