import os
import re
import io
import asyncio
import tempfile
from typing import Optional, List, Dict, Tuple
from urllib.parse import urljoin

import pandas as pd
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ------------------- Config via env -------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]        # from @BotFather
WEBHOOK_SECRET   = os.environ.get("WEBHOOK_SECRET", "supersecret")
BASE_URL         = "https://divar.ir"
HEADLESS         = os.environ.get("HEADLESS", "1") == "1"
VIEWPORT         = {"width": 1400, "height": 2800}
MAX_TIME_SEC     = int(os.environ.get("MAX_TIME_SEC", "180"))
STALL_ROUNDS     = int(os.environ.get("STALL_ROUNDS", "5"))
LOW_OUTLIER_F    = float(os.environ.get("LOW_OUTLIER_F", "1.5"))

# ------------------- Helpers -------------------
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
def fa_to_en(s: str) -> str:
    return (s or "").translate(PERSIAN_DIGITS).strip()

def parse_int_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    t = fa_to_en(text)
    t = re.sub(r"[^\d,_]", "", t).replace(",", "").replace("_", "")
    return int(t) if t.isdigit() else None

def parse_price(text: str) -> Optional[int]:
    if not text:
        return None
    t = text.strip()
    if any(kw in t for kw in ["توافقی", "بدون قیمت", "تماس"]):
        return None
    return parse_int_from_text(t)

def extract_cards_from_html(html: str, page_url: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows: List[Dict] = []
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
                bottom_el.get_text(strip=True) if bottom_el else ""
            )
            img_el = a.select_one(".kt-post-card-thumbnail img.kt-image-block__image")
            img = img_el.get("src") if img_el else ""
            tag_el = a.select_one(".kt-post-card__red-text")
            tag = tag_el.get_text(strip=True) if tag_el else ""

            rows.append({
                "title": title, "price": price, "price_text": price_text or "",
                "km": km, "km_text": km_text or "", "bottom": bottom, "tag": tag,
                "url": url, "image": img,
            })
        except Exception:
            continue
    return rows

async def scrape_infinite_collect(url: str) -> List[Dict]:
    """Scroll page, collect visible cards every loop (defeats virtualization), dedupe by URL."""
    seen: Dict[str, Dict] = {}
    import time
    start = time.time()
    stall = 0
    prev_seen = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        ctx = await browser.new_context(viewport=VIEWPORT, locale="fa-IR",
                                        user_agent=("Mozilla/5.0 (X11; Linux x86_64) "
                                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                    "Chrome/124.0 Safari/537.36"))
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_selector("article.kt-post-card", timeout=20000)

        while True:
            html = await page.content()
            for r in extract_cards_from_html(html, url):
                seen.setdefault(r["url"], r)

            dom_count = await page.locator("article.kt-post-card").count()

            # stop conditions
            if len(seen) == prev_seen:
                stall += 1
            else:
                stall = 0
            if stall >= STALL_ROUNDS or (time.time() - start) > MAX_TIME_SEC:
                break

            # try "show more"
            more_btn = page.locator("text=نمایش بیشتر, text=مشاهده آگهی‌های بیشتر, text=آگهی‌های بیشتر").first
            try:
                if await more_btn.count():
                    await more_btn.click(timeout=1500)
            except Exception:
                pass

            # nudge last card into view (IntersectionObserver)
            if dom_count:
                last = page.locator("article.kt-post-card").nth(dom_count - 1)
                try:
                    await last.scroll_into_view_if_needed(timeout=3000)
                except PlaywrightTimeoutError:
                    await page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.9));")
            else:
                await page.evaluate("window.scrollBy(0, 400);")

            # waits
            try:
                await page.wait_for_load_state("networkidle", timeout=1500)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(300)
            await page.evaluate("window.scrollBy(0, 200);")

            prev_seen = len(seen)

        await ctx.close()
        await browser.close()

    return list(seen.values())

def remove_low_price_outliers(rows: List[Dict], factor: float) -> List[Dict]:
    priced = [r for r in rows if isinstance(r.get("price"), int)]
    if len(priced) < 5:
        return rows
    prices = sorted(r["price"] for r in priced)
    s = pd.Series(prices)
    q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
    cutoff = q1 - factor * (q3 - q1)
    return [r for r in rows if (r.get("price") is None) or (r["price"] >= cutoff)]

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

# ------------------- Telegram bot -------------------
application = Application.builder().token(TELEGRAM_TOKEN).build()

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! Send /scrape <divar listing URL>\nExample:\n"
        "/scrape https://divar.ir/s/tehran/car"
    )

async def scrape_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /scrape <full list URL>")
        return
    url = ctx.args[0]
    msg = await update.message.reply_text("Scraping… this can take ~1–3 minutes.")
    try:
        rows = await scrape_infinite_collect(url)
        rows = remove_low_price_outliers(rows, LOW_OUTLIER_F)
        # sort ascending by price where available
        rows = sorted([r for r in rows if isinstance(r.get("price"), int)], key=lambda r: r["price"])
        xlsx = rows_to_excel_bytes(rows)
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(xlsx)
            tmp.flush()
            await update.message.reply_document(document=tmp.name, filename="cars.xlsx",
                                                caption=f"Found {len(rows)} priced ads.")
    except Exception as e:
        await update.message.reply_text(f"Failed: {e}")
    finally:
        await msg.delete()

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CommandHandler("scrape", scrape_cmd))

# ------------------- FastAPI webhook -------------------
api = FastAPI()

# ✅ NEW: initialize PTB on startup
@api.on_event("startup")
async def on_startup():
    await application.initialize()

# ✅ NEW: graceful shutdown
@api.on_event("shutdown")
async def on_shutdown():
    await application.shutdown()

@api.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"

@api.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    update = Update.de_json(data, application.bot)  # <- now bot is initialized
    await application.process_update(update)
    return {"ok": True}