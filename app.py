import os
import logging
import httpx
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from scraper import (
    scrape_infinite_collect,
    remove_low_price_outliers,
    sort_by_price,
    rows_to_excel_bytes,
)

# ================== Config ==================
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET", "supersecret")
APP_URL = os.environ.get("APP_URL")  # e.g. https://carscrapper-bot.onrender.com
PORT = int(os.environ.get("PORT", "10000"))

# Create Telegram application (no polling, webhook mode)
application = Application.builder().token(TELEGRAM_TOKEN).build()

# ================== Handlers ==================
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("👋 سلام! لینک دیوار را ارسال کنید.")

async def link_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if "http" not in text:
        return

    msg = await update.message.reply_text("⏳ درحال استخراج، لطفا منتظر بمانید...")

    try:
        rows = await scrape_infinite_collect(text)
        rows, _ = remove_low_price_outliers(rows, 1.5)
        rows = sort_by_price(rows)
        xlsx = rows_to_excel_bytes(rows)

        await update.message.reply_document(
            document=xlsx,
            filename="cars.xlsx",
            caption=f"📊 تعداد آگهی: {len(rows)}"
        )
    except Exception as e:
        logging.exception("Scrape failed")
        await update.message.reply_text(f"❌ خطا: {e}")
    finally:
        try:
            await msg.delete()
        except Exception:
            pass

# Register handlers
application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))

# ================== FastAPI wrapper ==================
api = FastAPI()

@api.on_event("startup")
async def startup_event():
    logging.info("🚀 Starting Telegram application")
    await application.initialize()
    await application.start()
    await application.bot.delete_webhook(drop_pending_updates=True)

    # Set webhook
    if APP_URL:
        url = f"{APP_URL}/webhook/{SECRET_TOKEN}"
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
                data={"url": url, "secret_token": SECRET_TOKEN},
            )
            logging.info(f"SetWebhook → {r.json()}")

@api.on_event("shutdown")
async def shutdown_event():
    logging.info("🛑 Stopping Telegram application")
    await application.stop()
    await application.shutdown()

@api.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if token != SECRET_TOKEN:
        return Response(status_code=403)

    data = await request.json()
    logging.info(f"📩 Incoming update: {data}")  # log raw Telegram payload
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)

    return {"ok": True}

# ================== Run locally ==================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:api", host="0.0.0.0", port=PORT)
