import os
import base58
from solana.keypair import Keypair
from solana.rpc.async_api import AsyncClient
from telegram import Bot
import asyncio

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # dạng Base58 Phantom

async def main():
    try:
        # Giải mã Base58 trực tiếp từ private key Phantom
        secret_key_bytes = base58.b58decode(PRIVATE_KEY)
        keypair = Keypair.from_secret_key(secret_key_bytes)

        client = AsyncClient("https://api.mainnet-beta.solana.com")
        balance = await client.get_balance(keypair.public_key)
        await client.close()

        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"PublicKey: {keypair.public_key}\nBalance: {balance['result']['value']} lamports"
        )
    except Exception as e:
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=f"Lỗi tạo keypair: {e}")

if __name__ == "__main__":
    asyncio.run(main())
