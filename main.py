import os
import asyncio
import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solana.rpc.async_api import AsyncClient
from telegram import Bot

# Lấy biến môi trường
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
PRIVATE_KEY_BASE58 = os.getenv("PRIVATE_KEY")

async def main():
    # --- Telegram Bot ---
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="BOT Solana AutoTrade khởi động!")

    # --- Chuyển đổi Private Key từ base58 -> Keypair ---
    try:
        private_key_bytes = base58.b58decode(PRIVATE_KEY_BASE58)
        keypair = Keypair.from_bytes(private_key_bytes[:64])
    except Exception as e:
        await bot.send_message(chat_id=CHAT_ID, text=f"Lỗi Private Key: {e}")
        return

    await bot.send_message(chat_id=CHAT_ID, text=f"Public key: {keypair.pubkey()}")

    # --- Kết nối RPC và lấy số dư ---
    async with AsyncClient("https://api.mainnet-beta.solana.com") as client:
        resp = await client.get_balance(keypair.pubkey())
        sol_balance = resp.value / 1e9 if resp.value else 0
        await bot.send_message(chat_id=CHAT_ID, text=f"SOL Balance: {sol_balance} SOL")

if __name__ == "__main__":
    asyncio.run(main())
