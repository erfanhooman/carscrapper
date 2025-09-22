import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from scraper import scrape_infinite_collect, remove_low_price_outliers, sort_by_price, rows_to_excel_bytes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

application = Application.builder().token(TELEGRAM_TOKEN).build()

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 لینک دیوار را ارسال نمایید"
    )

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
        await update.message.reply_text(f"❌ Failed: {e}")
    finally:
        await msg.delete()

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))

if __name__ == "__main__":
    application.run_polling()
