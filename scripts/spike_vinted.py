"""Проверка доступа к внутреннему API Vinted без headless-браузера.

Запуск: .venv\\Scripts\\python.exe scripts\\spike_vinted.py [домен]
Например: ... scripts\\spike_vinted.py www.vinted.fr
"""

import json
import sys

from curl_cffi import requests

DOMAIN = sys.argv[1] if len(sys.argv) > 1 else "www.vinted.com"
IMPERSONATE = "chrome"


def build_headers(domain: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://{domain}/",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def main() -> int:
    session = requests.Session(impersonate=IMPERSONATE, timeout=30)

    print(f"[1] GET https://{DOMAIN}/ ...")
    home = session.get(f"https://{DOMAIN}/", allow_redirects=True)
    print(f"    status={home.status_code} final_url={home.url}")

    cookies = {c.name: c.value for c in session.cookies.jar}
    interesting = [
        "access_token_web",
        "refresh_token_web",
        "datadome",
        "anon_id",
        "v_udt",
    ]
    print("    cookies:")
    for name in interesting:
        value = cookies.get(name)
        marker = "OK " if value else "-- "
        preview = f"{value[:28]}..." if value else "отсутствует"
        print(f"      {marker}{name}: {preview}")
    extra = sorted(set(cookies) - set(interesting))
    print(f"    прочие cookies ({len(extra)}): {', '.join(extra[:12])}")

    api_url = f"https://{DOMAIN}/api/v2/catalog/items"
    params = {"page": 1, "per_page": 20, "order": "newest_first"}
    print(f"\n[2] GET {api_url} ...")
    resp = session.get(api_url, params=params, headers=build_headers(DOMAIN))
    content_type = resp.headers.get("content-type")
    print(f"    status={resp.status_code} content-type={content_type}")

    if resp.status_code != 200:
        print(f"    тело (первые 400 символов):\n{resp.text[:400]}")
        return 1

    try:
        payload = resp.json()
    except json.JSONDecodeError:
        print(f"    не JSON:\n{resp.text[:400]}")
        return 1

    items = payload.get("items", [])
    print(f"    получено объявлений: {len(items)}")
    for item in items[:5]:
        price = (item.get("price") or {}).get("amount")
        currency = (item.get("price") or {}).get("currency_code")
        print(
            f"      #{item.get('id')} | {str(item.get('title'))[:40]:<40} | "
            f"{price} {currency} | {item.get('brand_title')}"
        )

    if items:
        with open("scripts/sample_item.json", "w", encoding="utf-8") as fh:
            json.dump(items[0], fh, ensure_ascii=False, indent=2)
        print("\n    первый объект сохранён в scripts/sample_item.json")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
