import os
import asyncio
import base58
from telegram import Bot
from solana.keypair import Keypair
from solana.rpc.async_api import AsyncClient
from solana.transaction import Transaction
from solana.rpc.types import TxOpts

# Lấy biến môi trường từ Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# Decode private key từ Base58
secret_key = base58.b58decode(PRIVATE_KEY)
keypair = Keypair.from_secret_key(secret_key)

bot = Bot(token=BOT_TOKEN)

async def main():
    # Khởi tạo kết nối Solana
    client = AsyncClient(RPC_URL)
    await bot.send_message(chat_id=CHAT_ID, text="BOT Solana AutoTrade khởi động!")

    # Ví dụ: lấy balance của ví
    balance = await client.get_balance(keypair.public_key)
    await bot.send_message(chat_id=CHAT_ID, text=f"Số dư ví: {balance['result']['value']} lamports")

    # Gửi giao dịch mẫu (nếu cần)
    # transaction = Transaction()
    # result = await client.send_transaction(transaction, keypair, opts=TxOpts(skip_preflight=True))
    # await bot.send_message(chat_id=CHAT_ID, text=f"Tx hash: {result['result']}")

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
