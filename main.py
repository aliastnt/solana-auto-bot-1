import os
import asyncio
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from telegram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

async def main():
    bot = Bot(token=BOT_TOKEN)

    # Load keypair từ private key
    keypair = Keypair.from_bytes(bytes.fromhex(PRIVATE_KEY))
    pubkey = keypair.pubkey()

    # Kết nối Solana
    client = AsyncClient("https://api.mainnet-beta.solana.com")
    balance = await client.get_balance(pubkey)
    
    # Gửi thông báo Telegram
    await bot.send_message(chat_id=CHAT_ID, text=f"BOT Solana AutoTrade khởi động!\nPublic Key: {pubkey}\nBalance: {balance['result']['value']} lamports")

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
