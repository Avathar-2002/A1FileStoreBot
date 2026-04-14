import os
import uuid
import httpx
import json

from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

# ---------------- ENV ----------------
TOKEN = os.getenv("TOKEN")
REDIS_URL = os.getenv("UPSTASH_REDIS_REST_URL")
REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
QSTASH_TOKEN = os.getenv("QSTASH_TOKEN")

# ---------------- TELEGRAM APP ----------------
telegram_app = ApplicationBuilder().token(TOKEN).build()

# ---------------- FASTAPI APP (THIS IS WHAT VERCEL NEEDS) ----------------
app = FastAPI()

# ---------------- REDIS ----------------
async def redis_set(key, value):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{REDIS_URL}/set/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            json={"value": json.dumps(value)}
        )

async def redis_get(key):
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{REDIS_URL}/get/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"}
        )
        data = res.json().get("result")
        return json.loads(data) if data else None

async def redis_delete(key):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{REDIS_URL}/del/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"}
        )

# ---------------- QSTASH ----------------
async def schedule_finalize(user_id):
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://qstash.upstash.io/v2/publish",
            headers={
                "Authorization": f"Bearer {QSTASH_TOKEN},
                "Content-Type": "application/json"
            },
            json={
                "url": "https://YOUR-APP.vercel.app/api/finalize",  # 🔥 CHANGE
                "delay": 10,
                "body": {"user_id": user_id}
            }
        )

# ---------------- HANDLER ----------------
async def handle(update: Update, context):
    user_id = str(update.effective_user.id)
    msg = update.message

    batch = await redis_get(user_id) or []

    if msg.text:
        batch.append({"type": "text", "content": msg.text})
    elif msg.photo:
        batch.append({"type": "photo", "file_id": msg.photo[-1].file_id})
    elif msg.document:
        batch.append({"type": "document", "file_id": msg.document.file_id})

    await redis_set(user_id, batch)
    await schedule_finalize(user_id)

    await msg.reply_text("✅ Added")

# ---------------- START ----------------
async def start(update: Update, context):
    if context.args:
        key = context.args[0]
        data = await redis_get(f"share:{key}")

        if not data:
            await update.message.reply_text("❌ Invalid")
            return

        for item in data:
            if item["type"] == "text":
                await update.message.reply_text(item["content"])
            elif item["type"] == "photo":
                await update.message.reply_photo(item["file_id"])
            elif item["type"] == "document":
                await update.message.reply_document(item["file_id"])
    else:
        await update.message.reply_text("Send files")

# ---------------- REGISTER ----------------
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.ALL, handle))

# ---------------- WEBHOOK ROUTE ----------------
@app.post("/api/bot")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)

    await telegram_app.initialize()
    await telegram_app.process_update(update)

    return {"ok": True}

# ---------------- FINALIZE ROUTE ----------------
@app.post("/api/finalize")
async def finalize(request: Request):
    data = await request.json()
    user_id = str(data["user_id"])

    batch = await redis_get(user_id)
    if not batch:
        return {"status": "empty"}

    key = str(uuid.uuid4())[:8]

    await redis_set(f"share:{key}", batch)
    await redis_delete(user_id)

    bot = Bot(TOKEN)
    username = (await bot.get_me()).username

    link = f"https://t.me/{username}?start={key}"

    await bot.send_message(chat_id=user_id, text=f"📦 Your link:\n{link}")

    return {"status": "done"}
