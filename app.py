import os
import asyncio
import logging
import httpx
from fastapi import FastAPI, Request, Response

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from scraper import scrape_infinite_collect, remove_low_price_outliers, sort_by_price, rows_to_excel_bytes

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET", "supersecret")
APP_URL = os.environ.get("APP_URL")  # e.g. https://your-service.onrender.com
PORT = int(os.environ.get("PORT", "10000"))

# Create telegram Application (no polling!)
application = Application.builder().token(TELEGRAM_TOKEN).build()

# === Handlers ===
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 لینک دیوار را ارسال نمایید")

async def link_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "http" not in text:
        return
    msg = await update.message.reply_text("⏳ درحال استخراج، لطفا منتظر بمانید")
    try:
        rows = await scrape_infinite_collect(text)
        rows, _ = remove_low_price_outliers(rows, 1.5)
        rows = sort_by_price(rows)
        xlsx = rows_to_excel_bytes(rows)
        await update.message.reply_document(
            document=xlsx,
            filename="cars.xlsx",
            caption=f"Found {len(rows)} priced ads."
        )
    except Exception as e:
        logging.exception("Scrape failed")
        await update.message.reply_text(f"❌ Failed: {e}")
    finally:
        await msg.delete()

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))

# === FastAPI wrapper ===
api = FastAPI()

@api.on_event("startup")
async def on_startup():
    # delete old webhook
    async with httpx.AsyncClient() as client:
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook")

    # set new webhook
    if APP_URL:
        url = f"{APP_URL}/webhook/{SECRET_TOKEN}"
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
                data={"url": url, "secret_token": SECRET_TOKEN}
            )
            logging.info(f"SetWebhook → {r.json()}")

@api.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if token != SECRET_TOKEN:
        return Response(status_code=403)
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

# === Run with Uvicorn ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:api", host="0.0.0.0", port=PORT)
