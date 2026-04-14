
import os
import uuid
import httpx
from fastapi import Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

TOKEN = os.getenv("8653928586:AAFcM38kPOn65Q0_oSSjoL9irzVQ31mqLlM")
REDIS_URL = os.getenv("https://relative-leopard-66853.upstash.io")
REDIS_TOKEN = os.getenv("gQAAAAAAAQUlAAIncDJmOTk0ZDU3NDMxNTU0NWE0YjgyNTU4MmM4ZmUxYjBkN3AyNjY4NTM")
QSTASH_TOKEN = os.getenv("eyJVc2VySUQiOiJlNmIxMTkyMS1mZThmLTQyZjctYjZhNC00NTMwOWM5MDZjYzgiLCJQYXNzd29yZCI6ImEwMjMxZGEwOWJhYTQwYmVhY2ZhYThkZGQyNTk1YjA2In0=")

app = ApplicationBuilder().token(TOKEN).build()

# ---------------- REDIS HELPERS ----------------
async def redis_set(key, value):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{REDIS_URL}/set/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            json=value
        )

async def redis_get(key):
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{REDIS_URL}/get/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"}
        )
        return res.json().get("result")

async def redis_delete(key):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{REDIS_URL}/del/{key}",
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"}
        )

# ---------------- QSTASH DELAY ----------------
async def schedule_finalize(user_id):
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://qstash.upstash.io/v2/publish",
            headers={
                "Authorization": f"Bearer {QSTASH_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "url": "https://your-vercel-url.vercel.app/api/finalize",
                "delay": 10,
                "body": {"user_id": user_id}
            }
        )

# ---------------- HANDLE MESSAGES ----------------
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

    # schedule new finalize (auto 10 sec)
    await schedule_finalize(user_id)

    await msg.reply_text("✅ Added (auto batching...)")

# ---------------- FINALIZE ----------------
async def finalize(request: Request):
    data = await request.json()
    user_id = str(data["user_id"])

    batch = await redis_get(user_id)
    if not batch:
        return {"status": "empty"}

    key = str(uuid.uuid4())[:8]

    await redis_set(f"share:{key}", batch)
    await redis_delete(user_id)

    from telegram import Bot
    bot = Bot(TOKEN)

    link = f"https://t.me/{(await bot.get_me()).username}?start={key}"

    await bot.send_message(chat_id=user_id, text=f"📦 Your link:\n{link}")

    return {"status": "done"}

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
        await update.message.reply_text("Send files, auto link in 10 sec")

# ---------------- REGISTER ----------------
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.ALL, handle))

# ---------------- MAIN HANDLER ----------------
async def handler(request: Request):
    data = await request.json()
    update = Update.de_json(data, app.bot)

    await app.initialize()
    await app.process_update(update)

    return {"ok": True}
