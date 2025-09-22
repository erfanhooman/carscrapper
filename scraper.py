import re, time, io
from typing import Optional, List, Dict, Tuple
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://divar.ir"
HEADLESS = True
VIEWPORT = {"width": 1400, "height": 2800}
MAX_TIME_SEC = 240
STALL_ROUNDS = 6
NETWORK_IDLE_MS = 1500
SMALL_WAIT_MS = 300

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

def fa_to_en(s: str) -> str:
    return (s or "").translate(PERSIAN_DIGITS).strip()

def parse_int_from_text(text: str) -> Optional[int]:
    if not text: return None
    t = fa_to_en(text)
    t = re.sub(r"[^\d,_]", "", t).replace(",", "").replace("_", "")
    return int(t) if t.isdigit() else None

def parse_price(text: str) -> Optional[int]:
    if not text: return None
    if any(kw in text for kw in ["توافقی", "بدون قیمت", "تماس"]):
        return None
    return parse_int_from_text(text)

def extract_cards_from_soup(soup: BeautifulSoup, page_url: str) -> List[Dict]:
    rows = []
    for a in soup.select("article.kt-post-card a.kt-post-card__action"):
        try:
            href = a.get("href") or ""
            url = urljoin(BASE_URL if href.startswith("/") else page_url, href)
            title_el = a.select_one(".kt-post-card__title")
            title = title_el.get_text(strip=True) if title_el else ""
            descs = [d.get_text(strip=True) for d in a.select(".kt-post-card__description")]
            km_text = descs[0] if len(descs) >= 1 else None
            price_text = descs[1] if len(descs) >= 2 else None
            km = parse_int_from_text(km_text) if km_text else None
            price = parse_price(price_text) if price_text else None
            bottom_el = a.select_one(".kt-post-card__bottom-description")
            bottom = bottom_el.get("title") if bottom_el and bottom_el.has_attr("title") else (
                bottom_el.get_text(strip=True) if bottom_el else "")
            img_el = a.select_one(".kt-post-card-thumbnail img.kt-image-block__image")
            img = img_el.get("src") if img_el else ""
            tag_el = a.select_one(".kt-post-card__red-text")
            tag = tag_el.get_text(strip=True) if tag_el else ""
            rows.append({
                "title": title, "price": price, "price_text": price_text or "",
                "km": km, "km_text": km_text or "", "bottom": bottom, "tag": tag,
                "url": url, "image": img
            })
        except Exception:
            continue
    return rows

async def scrape_infinite_collect(url: str,
                                  headless: bool = HEADLESS,
                                  viewport: Dict = VIEWPORT,
                                  max_time_sec: int = MAX_TIME_SEC,
                                  stall_rounds: int = STALL_ROUNDS) -> List[Dict]:
    seen: Dict[str, Dict] = {}
    start = time.time()
    stall = 0
    prev_seen = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(viewport=viewport, locale="fa-IR")
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_selector("article.kt-post-card", state="attached", timeout=20000)

        while True:
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            batch = extract_cards_from_soup(soup, url)
            for r in batch:
                seen.setdefault(r["url"], r)
            dom_count = await page.locator("article.kt-post-card").count()
            if len(seen) == prev_seen: stall += 1
            else: stall = 0
            if stall >= stall_rounds or (time.time() - start) > max_time_sec:
                break
            if dom_count:
                last = page.locator("article.kt-post-card").nth(dom_count - 1)
                try:
                    await last.scroll_into_view_if_needed(timeout=3000)
                except PlaywrightTimeoutError:
                    await page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.9));")
            else:
                await page.evaluate("window.scrollBy(0, 400);")
            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(SMALL_WAIT_MS)
            prev_seen = len(seen)
        await ctx.close(); await browser.close()
    return list(seen.values())

def remove_low_price_outliers(rows: List[Dict], factor: float) -> Tuple[List[Dict], Dict]:
    priced = [r for r in rows if isinstance(r.get("price"), int)]
    if len(priced) < 5:
        return rows, {"dropped":0,"q1":None,"q3":None,"iqr":None,"cutoff":None}
    prices = sorted(r["price"] for r in priced)
    s = pd.Series(prices)
    q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
    iqr = q3 - q1
    cutoff = q1 - factor*iqr
    filtered = [r for r in rows if (r.get("price") is None) or (r["price"] >= cutoff)]
    return filtered, {"dropped": len(rows)-len(filtered),"q1":q1,"q3":q3,"iqr":iqr,"cutoff":cutoff}

def sort_by_price(rows: List[Dict]) -> List[Dict]:
    return sorted([r for r in rows if isinstance(r.get("price"), int)], key=lambda r: r["price"])

def rows_to_excel_bytes(rows: List[Dict]) -> bytes:
    df = pd.DataFrame(rows)
    if "price" in df:
        df["price_formatted"] = df["price"].apply(lambda x: f"{x:,}" if pd.notnull(x) else "")
    if "km" in df:
        df["km_formatted"] = df["km"].apply(lambda x: f"{int(x):,}" if pd.notnull(x) else "")
    preferred = ["title","price","price_formatted","km","km_formatted","bottom","tag","url","image","price_text","km_text"]
    df = df[[c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="cars")
    return buf.getvalue()
