from __future__ import annotations

import json
from typing import Any


DEFAULT_RESOURCE_KEYWORDS = (
    "/api/",
    "pipo",
    "pay/",
    "settlement",
    "download",
    "merchant/file/export",
    "fund/detail",
    "anti-content",
)


def js_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def diagnostic_enabled(task: dict[str, Any]) -> bool:
    return bool(task.get("diagnostic_mode"))


def install_browser_request_recorder(page: Any, max_entries: int = 80) -> bool:
    script = f"""
        return (() => {{
            if (window.__financeCrawlerRecorderInstalled) return true;
            window.__financeCrawlerRecorderInstalled = true;
            window.__financeCrawlerRequests = window.__financeCrawlerRequests || [];
            const maxEntries = {int(max_entries)};
            const pushEntry = (entry) => {{
                try {{
                    window.__financeCrawlerRequests.push({{
                        time: new Date().toISOString(),
                        method: String(entry.method || 'GET').toUpperCase(),
                        url: String(entry.url || ''),
                        status: entry.status ?? null,
                        ok: entry.ok ?? null,
                        requestBody: entry.requestBody ?? null,
                        responseText: entry.responseText ? String(entry.responseText).slice(0, 4000) : '',
                        error: entry.error ? String(entry.error).slice(0, 1000) : ''
                    }});
                    if (window.__financeCrawlerRequests.length > maxEntries) {{
                        window.__financeCrawlerRequests.splice(0, window.__financeCrawlerRequests.length - maxEntries);
                    }}
                }} catch (e) {{}}
            }};
            const originalFetch = window.fetch;
            if (typeof originalFetch === 'function') {{
                window.fetch = async (...args) => {{
                    const input = args[0];
                    const init = args[1] || {{}};
                    const method = init.method || (input && input.method) || 'GET';
                    const url = typeof input === 'string' ? input : String((input && input.url) || '');
                    const requestBody = typeof init.body === 'string' ? init.body.slice(0, 4000) : '';
                    try {{
                        const response = await originalFetch.apply(window, args);
                        const clone = response.clone();
                        let responseText = '';
                        try {{ responseText = await clone.text(); }} catch (e) {{}}
                        pushEntry({{method, url, status: response.status, ok: response.ok, requestBody, responseText}});
                        return response;
                    }} catch (error) {{
                        pushEntry({{method, url, requestBody, error}});
                        throw error;
                    }}
                }};
            }}
            return true;
        }})();
    """
    try:
        return bool(page.run_js(script))
    except Exception:
        return False


def collect_browser_diagnostics(page: Any, keywords: tuple[str, ...] = DEFAULT_RESOURCE_KEYWORDS) -> dict[str, Any]:
    script = f"""
        return (() => {{
            const keywords = {js_json(list(keywords))};
            const matches = (value) => {{
                const text = String(value || '').toLowerCase();
                return keywords.some((item) => text.includes(String(item).toLowerCase()));
            }};
            const resources = [];
            try {{
                performance.getEntriesByType('resource').forEach((entry) => {{
                    if (matches(entry.name)) resources.push(entry.name);
                }});
            }} catch (e) {{}}
            const requests = Array.isArray(window.__financeCrawlerRequests)
                ? window.__financeCrawlerRequests.filter((entry) => matches(entry.url) || matches(entry.responseText)).slice(-80)
                : [];
            return {{
                url: String(location.href || ''),
                title: String(document.title || ''),
                requests,
                resources: Array.from(new Set(resources)).slice(-80)
            }};
        }})();
    """
    try:
        data = page.run_js(script)
    except Exception as exc:
        return {"error": str(exc)}
    return data if isinstance(data, dict) else {"raw": data}
