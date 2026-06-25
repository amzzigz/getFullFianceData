# E1E2 DrissionPage Readiness Design

## Goal

Replace E1E2 fixed sleeps and custom document-state polling with DrissionPage-native navigation waits and a business-request readiness signal.

## Scope

- Only `tiktok_email_income`.
- Do not change ordinary TikTok, SHEIN, TEMU, or Shenhe flows.
- Preserve account-level serial execution and existing no-data handling.

## Design

Before navigating to the Bills page, start the DrissionPage listener for
`/api/v3/seller/common/get`. Navigate with `page.get()`, then use
`page.wait.url_change()` and `page.wait.doc_loaded()` as navigation guards.
The decisive readiness signal is the matching seller API packet, because
document loading alone does not cover SPA JavaScript and secondary redirects.

If the packet contains a successful JSON response, parse seller information
directly from `packet.response.body` and skip the immediate browser-side fetch.
If the listener is unavailable or times out, fall back to the existing
`get_seller_info()` request path. The existing short retry for DrissionPage's
page-refresh exception remains defense in depth, not the primary readiness
mechanism.

Always stop the listener in `finally` so later requests are not consumed by a
stale target queue.

## Success Criteria

- No fixed sleep is used to decide whether the Bills page is ready.
- Listener starts before navigation.
- URL and document waits use DrissionPage wait APIs.
- A successful seller packet supplies `seller_id` without a second request.
- Listener timeout falls back to the existing request path.
- Existing E1 no-data and E2 export behavior remains unchanged.

