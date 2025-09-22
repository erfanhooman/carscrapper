import os
import io
import tempfile
import asyncio
from typing import List, Dict

import pandas as pd
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==== import your working scraper ====
from scraper import scrape_infinite_collect, remove_low_price_outliers, sort_by_price
from scraper import rows_to_excel_bytes   # helper we’ll add below

# ============== Config ==============
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "supersecret")
LOW_OUTLIER_F = float(os.environ.get("LOW_OUTLIER_F", "1.5"))

# ============== Telegram Bot ==============
application = Application.builder().token(TELEGRAM_TOKEN).build()

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 لینک دیوار را ارسال کنید (مثل https://divar.ir/s/tehran/car?q=...) "
    )

async def link_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "http" not in text:
        return
    msg = await update.message.reply_text("⏳ درحال استخراج، منتظر بمانید")
    try:
        # run scraper
        rows = await scrape_infinite_collect(text)
        rows, _ = remove_low_price_outliers(rows, factor=LOW_OUTLIER_F)
        rows = sort_by_price(rows)
        # convert to Excel
        xlsx = rows_to_excel_bytes(rows)
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(xlsx)
            tmp.flush()
            await update.message.reply_document(
                document=tmp.name,
                filename="cars.xlsx",
                caption=f"Found {len(rows)} priced ads."
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")
    finally:
        await msg.delete()

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))

# ============== FastAPI wrapper ==============
api = FastAPI()

@api.on_event("startup")
async def on_startup():
    await application.initialize()

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
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}
