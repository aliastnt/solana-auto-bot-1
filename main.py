import os
import asyncio
from telegram import Bot
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solana.rpc.async_api import AsyncClient

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

async def main():
    bot = Bot(token=BOT_TOKEN)

    # Tạo keypair từ private key hex
    try:
        secret = bytes.fromhex(PRIVATE_KEY)
        keypair = Keypair.from_bytes(secret)
    except Exception as e:
        await bot.send_message(chat_id=CHAT_ID, text=f"Lỗi tạo keypair: {e}")
        return

    pubkey = keypair.pubkey()

    # Kết nối Solana
    client = AsyncClient("https://api.mainnet-beta.solana.com")
    version = await client.get_version()

    # Gửi thông báo Telegram
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"BOT Solana AutoTrade khởi động!\nPublic Key: {pubkey}\nNode Version: {version.value}"
    )

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
