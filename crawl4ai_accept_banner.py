#!/usr/bin/env python3
"""
Crawl4AI mit js_code fuer Accept-Banner / Cookie-Banner
Option C: Automatisch Banner klicken BEVOR gecrawlt wird

Verwendung:
    python crawl4ai_accept_banner.py <URL> [--banner-id ID] [--banner-text TEXT]
"""
import json
import requests
import sys
import argparse

API_URL = "http://localhost:11235/crawl"
TOKEN = "testtoken123"

# Typische Accept-Banner Selectoren
ACCEPT_BANNER_SELECTORS = [
    # GDPR / Cookie Banner
    "#onetrust-accept-btn-handler",       # OneTrust
    "#onetrust-consent-sdk button.accept",  # OneTrust alt
    ".optanon-button-accept",              # OneTrust alt
    "button[aria-label='Accept']",
    "button[id*='accept']",
    "a[id*='accept']",
    "[class*='cookie-consent'] button",    # Generisch
    "[class*='cookie-banner'] button",     # Generisch
    "[id*='cookie'] button",              # Generisch
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",  # Cybot
    ".cc-btn.cc-accept",                  # Cookie Consent (CookieBot)
    "#cookie-accept",
    "#cookieConsentAccept",
    "#accept-cookies",
    "button.accept",
    "button[data-action='accept']",
    # Finviz-spezifisch
    ".accept-all-cookies",
    ".sc-2f5e98c7-4 button",            # Finviz Options
]

# JavaScript zum Klicken aller moeglichen Accept-Buttons
JS_ACCEPT_ALL = """
(function() {
    var selectors = [
        "#onetrust-accept-btn-handler",
        ".optanon-button-accept",
        "button[aria-label='Accept']",
        "button[id*='accept']",
        "[class*='cookie-consent'] button",
        "[class*='cookie-banner'] button",
        "[id*='cookie'] button",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        ".cc-btn.cc-accept",
        "#cookie-accept",
        "#cookieConsentAccept",
        "#accept-cookies",
        "button.accept",
        "button[data-action='accept']",
        ".accept-all-cookies",
        "button[class*='accept']",
        "a[class*='accept']",
        ".sc-2f5e98c7-4 button",
        "#gdpr-consent-banner button",
        ".consent-banner button",
        ".modal-footer button.btn-primary",
        "button[type='submit']",
    ];
    var clicked = 0;
    selectors.forEach(function(sel) {
        try {
            var el = document.querySelector(sel);
            if (el && el.offsetParent !== null) {
                el.click();
                clicked++;
            }
        } catch(e) {}
    });
    return "Clicked " + clicked + " accept buttons";
})();
"""

# JavaScript: Accept-Banner mit spezifischem Text suchen und klicken
JS_ACCEPT_BY_TEXT = """
function clickAcceptButton(text) {
    var buttons = Array.from(document.querySelectorAll('button, a, input[type="button"], [role="button"]'));
    var clicked = 0;
    buttons.forEach(function(btn) {
        var t = (btn.textContent || btn.value || '').trim().toLowerCase();
        if (t.includes(text.toLowerCase())) {
            btn.click();
            clicked++;
        }
    });
    return clicked + ' button(s) clicked';
}
clickAcceptButton('{TEXT}');
"""

def crawl_with_accept(url, banner_text=None, headless=True, verbose=False, wait_for=None, timeout=60):
    """
    Crawlt eine URL und klickt zunaechst alle Accept-Banner.

    Args:
        url: Die URL zum Crawlen
        banner_text: Optionaler Text im Accept-Button (z.B. "Alle akzeptieren")
        headless: Browser headless?
        verbose: Mehr Ausgabe?
        wait_for: CSS-Selector auf den gewartet wird
        timeout: Timeout in Sekunden

    Returns:
        Crawl4AI Ergebnis als JSON
    """
    payload = {
        "urls": [url],
        "crawler_params": {
            "headless": headless,
            "verbose": verbose,
            "js_code": JS_ACCEPT_ALL if not banner_text else JS_ACCEPT_BY_TEXT.replace("{TEXT}", banner_text),
            "wait_for": wait_for or "body",  # Warten bis body geladen
            "page_timeout": timeout * 1000,
        },
        "extraction_params": {
            "extraction_strategy": "NoExtractionStrategy",  # Rohes HTML
        },
        "processor_params": {
            "processor": "BeautifulSoupProcessor",
            "params": {
                "extract_tags": ["table", "tbody", "tr", "td", "h1", "h2", "h3", "title", "a"],
                "exclude_tags": ["script", "style", "noscript", "iframe"],
            }
        }
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}"
    }

    print(f"[Crawl4AI] Crawle: {url}")
    print(f"[Crawl4AI] Accept-JS: {'Generisch (alle Banner)' if not banner_text else 'Text: ' + banner_text}")

    try:
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=timeout + 10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[Crawl4AI] Fehler: {e}", file=sys.stderr)
        if hasattr(e, 'response') and e.response is not None:
            print(f"[Crawl4AI] Response: {e.response.text[:500]}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Crawl4AI mit Accept-Banner-Handling")
    parser.add_argument("url", help="URL zum Crawlen")
    parser.add_argument("--banner-text", default=None, help="Text im Accept-Button (z.B. 'Alle akzeptieren')")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--verbose", action="store_true", default=False)
    parser.add_argument("--wait-for", default=None, help="CSS-Selector auf den gewartet wird")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output", default=None, help="Output-Datei (JSON)")
    args = parser.parse_args()

    result = crawl_with_accept(
        url=args.url,
        banner_text=args.banner_text,
        headless=args.headless,
        verbose=args.verbose,
        wait_for=args.wait_for,
        timeout=args.timeout
    )

    # Ergebnis ausgeben
    print("\n=== Crawl4AI Ergebnis ===")
    if "results" in result and len(result["results"]) > 0:
        r = result["results"][0]
        print(f"URL: {r.get('url', 'N/A')}")
        print(f"Status: {r.get('status', 'N/A')}")

        # In v0.9.2 ist das Ergebnis ein Dict mit 'html' und/oder 'markdown'
        html_content = r.get("html", "")
        markdown_content = r.get("markdown", "")
        extracted_content = r.get("extracted_content", "")

        if html_content and isinstance(html_content, str):
            content = html_content
            print(f"Laenge (HTML): {len(content)} Zeichen")
            print(f"\n--- HTML (erste 1500 Zeichen) ---")
            print(content[:1500])
        elif markdown_content and isinstance(markdown_content, str):
            content = markdown_content
            print(f"Laenge (Markdown): {len(content)} Zeichen")
            print(f"\n--- Markdown (erste 1500 Zeichen) ---")
            print(content[:1500])
        elif extracted_content and isinstance(extracted_content, str):
            content = extracted_content
            print(f"Laenge (Extracted): {len(content)} Zeichen")
            print(f"\n--- Extracted (erste 1500 Zeichen) ---")
            print(content[:1500])
        else:
            # Fallback: zeige alle Felder
            print(f"Verfuegbare Felder: {list(r.keys())}")
            print(json.dumps(r, indent=2)[:2000])
    else:
        print(json.dumps(result, indent=2)[:2000])

    # Optional speichern
    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nErgebnis gespeichert: {args.output}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nBeispiele:")
        print("  python crawl4ai_accept_banner.py https://finviz.com/screener.ashx?v=152")
        print("  python crawl4ai_accept_banner.py https://example.com --banner-text 'Alle akzeptieren'")
        print("  python crawl4ai_accept_banner.py https://example.com --verbose --output result.json")
        sys.exit(1)
    main()
