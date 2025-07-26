import os
import logging
import asyncio
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.hash import Hash
from solders.message import MessageV0
from solders.transaction import VersionedTransaction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = os.getenv("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")

bot = Bot(token=BOT_TOKEN)

def load_keypair():
    secret = [int(x) for x in PRIVATE_KEY.split(",")]
    return Keypair.from_bytes(bytes(secret))

keypair = load_keypair()
client = AsyncClient(SOLANA_ENDPOINT)

async def send_telegram_message(text):
    await bot.send_message(chat_id=CHAT_ID, text=text)

async def check_and_trade():
    try:
        # Giá token (ví dụ: SOL/USDC)
        url = "https://api.dexscreener.io/latest/dex/pairs/solana/So11111111111111111111111111111111111111112"
        r = requests.get(url).json()
        price = r["pairs"][0]["priceUsd"]
        logger.info(f"Price: {price}")

        if float(price) > 100:  # ví dụ test
            await send_telegram_message(f"Giá {price}, gửi lệnh thử 0.01 SOL...")

            recent_blockhash = (await client.get_latest_blockhash()).value.blockhash
            message = MessageV0.try_compile(
                payer=keypair.pubkey(),
                instructions=[],
                recent_blockhash=Hash.from_string(recent_blockhash)
            )
            tx = VersionedTransaction(message, [keypair])
            sig = await client.send_transaction(tx)
            await send_telegram_message(f"Lệnh chính gửi thành công, sig: {sig.value}")
    except Exception as e:
        logger.error(e)
        await send_telegram_message(f"Lỗi giao dịch: {str(e)}")

async def main():
    await send_telegram_message("BOT Solana AutoTrade (Full Auto) đã khởi động!")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_trade, "interval", seconds=30)
    scheduler.start()
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
