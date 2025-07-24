import os
import asyncio
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from solana.keypair import Keypair
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from solana.rpc.types import TxOpts
from solana.publickey import PublicKey

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Solana Client
client = Client("https://api.mainnet-beta.solana.com")

# Tạo Keypair từ Private Key
keypair = Keypair.from_secret_key(bytes.fromhex(PRIVATE_KEY))
pubkey = keypair.public_key

# ==== HANDLERS ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("Bạn không có quyền sử dụng bot này!")
        return
    balance = client.get_balance(pubkey)['result']['value'] / 1_000_000_000
    await update.message.reply_text("BOT Solana AutoTrade khởi động!")
    await update.message.reply_text(f"Public key: {pubkey}")
    await update.message.reply_text(f"Số dư hiện tại: {balance} SOL")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("Bạn không có quyền sử dụng bot này!")
        return
    msg = (
        "Danh sách lệnh:\n"
        "/start - Khởi động bot và hiển thị thông tin ví\n"
        "/balance - Kiểm tra số dư ví\n"
        "/send <địa_chỉ_nhận> <số_SOL> - Gửi SOL tới địa chỉ khác\n"
        "/help - Hiển thị danh sách lệnh"
    )
    await update.message.reply_text(msg)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("Bạn không có quyền sử dụng bot này!")
        return
    balance = client.get_balance(pubkey)['result']['value'] / 1_000_000_000
    await update.message.reply_text(f"Số dư hiện tại: {balance} SOL")

async def send_sol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("Bạn không có quyền sử dụng bot này!")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Cú pháp: /send <địa_chỉ_nhận> <số_SOL>")
        return

    try:
        dest = PublicKey(context.args[0])
        amount_sol = float(context.args[1])
        lamports = int(amount_sol * 1_000_000_000)

        txn = Transaction().add(
            transfer(TransferParams(
                from_pubkey=pubkey,
                to_pubkey=dest,
                lamports=lamports
            ))
        )

        resp = client.send_transaction(txn, keypair, opts=TxOpts(skip_preflight=True))
        await update.message.reply_text(f"Đã gửi {amount_sol} SOL đến {dest}\nTransaction: {resp['result']}")
    except Exception as e:
        await update.message.reply_text(f"Lỗi khi gửi SOL: {str(e)}")

# ==== MAIN ====
async def main():
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="BOT Solana AutoTrade khởi động!")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("send", send_sol))

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
