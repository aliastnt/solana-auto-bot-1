import os
import asyncio
from telegram import Bot
from solana.keypair import Keypair
from solana.rpc.api import Client

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Khởi tạo Solana client
client = Client("https://api.mainnet-beta.solana.com")

async def start_bot():
    bot = Bot(token=BOT_TOKEN)
    # Gửi thông báo khởi động
    await bot.send_message(chat_id=CHAT_ID, text="BOT Solana AutoTrade khởi động!")

    # Tạo keypair từ PRIVATE_KEY
    keypair = Keypair.from_secret_key(bytes.fromhex(PRIVATE_KEY))
    pubkey = keypair.public_key

    # Gửi public key để kiểm tra
    await bot.send_message(chat_id=CHAT_ID, text=f"Public key: {pubkey}")

    # Lấy số dư ví
    balance = client.get_balance(pubkey)['result']['value'] / 1_000_000_000
    await bot.send_message(chat_id=CHAT_ID, text=f"Số dư: {balance} SOL")

if __name__ == "__main__":
    asyncio.run(start_bot())
