# main.py - Solana Auto Trading Bot (Final)

import os
import logging
import base64
import asyncio
import threading
import json
import requests
import base58

from telegram.ext import Updater, CommandHandler
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.transaction import Transaction
from solana.rpc.api import Client
import pytz
from apscheduler.schedulers.background import BackgroundScheduler

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------- Environment ----------------
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
PRIVATE_KEY = os.getenv('PRIVATE_KEY')
RPC_URL = os.getenv('SOLANA_ENDPOINT')

missing_vars = []
if not TELEGRAM_TOKEN: missing_vars.append("TELEGRAM_BOT_TOKEN")
if not CHAT_ID: missing_vars.append("CHAT_ID")
if not PRIVATE_KEY: missing_vars.append("PRIVATE_KEY")
if not RPC_URL: missing_vars.append("SOLANA_ENDPOINT")
if missing_vars:
    logger.error(f"Missing environment variables: {', '.join(missing_vars)}")
    exit(1)

# ---------------- Wallet init ----------------
try:
    secret_key_bytes = base58.b58decode(PRIVATE_KEY)
    trader_keypair = Keypair.from_bytes(secret_key_bytes)
    wallet_address = str(trader_keypair.pubkey())
except Exception as e:
    logger.error(f"Failed to load wallet keypair: {e}")
    exit(1)
logger.info(f"Wallet address: {wallet_address}")

solana_client = Client(RPC_URL)

# ---------------- Telegram ----------------
updater = Updater(TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# ---------------- Global trade state ----------------
current_trade = None
trade_lock = threading.Lock()

# ---------------- DexScreener trending fetch ----------------
async def fetch_trending_pairs():
    import websockets
    uri = (
        "wss://io.dexscreener.com/dex/screener/pairs/h24/1"
        "?chain=solana&rankBy[key]=trendingScoreM5&rankBy[order]=desc"
    )
    async with websockets.connect(uri, ping_timeout=20, close_timeout=5) as websocket:
        return await websocket.recv()

def get_trending_pairs():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        data_str = loop.run_until_complete(fetch_trending_pairs())
    except Exception as e:
        logger.error(f"Failed to fetch trending pairs: {e}")
        return []
    try:
        data = json.loads(data_str)
        return data.get("pairs", []) if data.get("type") == "pairs" else []
    except Exception as e:
        logger.error(f"Failed to parse trending data: {e}")
        return []

# ---------------- Jupiter Swap ----------------
def execute_swap(input_mint, output_mint, amount_in, slippage_bps=50):
    quote_url = (
        f"https://quote-api.jup.ag/v6/quote?"
        f"inputMint={input_mint}&outputMint={output_mint}&amount={amount_in}&slippageBps={slippage_bps}"
    )
    try:
        quote_resp = requests.get(quote_url)
        quote_data = quote_resp.json()
    except Exception as e:
        logger.error(f"Jupiter quote API error: {e}")
        return False

    if not quote_data.get("data"):
        logger.info("No route found on Jupiter for swap.")
        return False

    swap_req = {
        "quoteResponse": quote_data,
        "userPublicKey": wallet_address,
        "wrapAndUnwrapSol": True,
        "asLegacyTransaction": True,
    }
    try:
        swap_resp = requests.post("https://quote-api.jup.ag/v6/swap", json=swap_req)
        swap_data = swap_resp.json()
    except Exception as e:
        logger.error(f"Jupiter swap API error: {e}")
        return False

    swap_tx_base64 = swap_data.get("swapTransaction")
    if not swap_tx_base64:
        logger.error("No transaction returned from Jupiter swap API")
        return False

    try:
        tx_bytes = base64.b64decode(swap_tx_base64)
        txn = Transaction.deserialize(tx_bytes)
        solana_client.send_transaction(txn, trader_keypair)
        return True
    except Exception as e:
        logger.error(f"Failed to send transaction: {e}")
        return False

# ---------------- Scheduled Scan ----------------
def scheduled_scan():
    global current_trade
    pairs = get_trending_pairs()
    if not pairs:
        return

    for pair in pairs:
        try:
            if pair.get("chainId") != "solana":
                continue
            vol_5m = float(pair.get("volume", {}).get("m5", 0) or 0)
            pc_5m = float(pair.get("priceChange", {}).get("m5", 0) or 0)
            if vol_5m >= 50000 and pc_5m >= 2:
                token_addr = pair.get("baseToken", {}).get("address")
                token_symbol = pair.get("baseToken", {}).get("symbol", "Unknown")
                with trade_lock:
                    if current_trade:
                        continue
                    current_trade = {"token_address": token_addr, "token_symbol": token_symbol}

                updater.bot.send_message(
                    chat_id=int(CHAT_ID),
                    text=f"Detected {token_symbol} - Vol 5m ${vol_5m:.0f}, Change 5m {pc_5m:.2f}%\nExecuting trade..."
                )

                SOL_MINT = "So11111111111111111111111111111111111111112"
                test_amount = int(0.01 * 1e9)

                if not execute_swap(SOL_MINT, token_addr, test_amount):
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                                             text=f"Test buy failed for {token_symbol}")
                    with trade_lock:
                        current_trade = None
                    continue

                if not execute_swap(token_addr, SOL_MINT, test_amount):
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                                             text=f"Test sell failed for {token_symbol}")
                    with trade_lock:
                        current_trade = None
                    continue

                balance = solana_client.get_balance(Pubkey.from_string(wallet_address))
                trade_amount = int(balance['result']['value'] * 0.3)

                if trade_amount < 1000:
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                                             text=f"Insufficient balance for main trade.")
                    with trade_lock:
                        current_trade = None
                    continue

                if execute_swap(SOL_MINT, token_addr, trade_amount):
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                                             text=f"Bought {token_symbol} with {trade_amount/1e9:.4f} SOL")
                    current_trade['amount'] = trade_amount
                else:
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                                             text=f"Main buy failed for {token_symbol}")
                    with trade_lock:
                        current_trade = None

        except Exception as e:
            logger.error(f"Error in scheduled_scan: {e}")

# ---------------- Manual Telegram Commands ----------------
def manual_buy(update, context):
    args = context.args
    if not args:
        update.message.reply_text("Usage: /buy <token_address> [percent_of_SOL_balance]")
        return

    token_addr = args[0]
    percent = float(args[1]) if len(args) > 1 else 30.0

    with trade_lock:
        if current_trade:
            update.message.reply_text("Trade already active.")
            return
        current_trade = {"token_address": token_addr, "token_symbol": token_addr}

    SOL_MINT = "So11111111111111111111111111111111111111112"
    test_amount = int(0.01 * 1e9)

    if not execute_swap(SOL_MINT, token_addr, test_amount):
        update.message.reply_text("Test buy failed.")
        with trade_lock:
            current_trade = None
        return
    if not execute_swap(token_addr, SOL_MINT, test_amount):
        update.message.reply_text("Test sell failed.")
        with trade_lock:
            current_trade = None
        return

    balance = solana_client.get_balance(Pubkey.from_string(wallet_address))
    trade_amount = int(balance['result']['value'] * (percent / 100.0))

    if trade_amount < 1000:
        update.message.reply_text("Insufficient balance for main trade.")
        with trade_lock:
            current_trade = None
        return

    if execute_swap(SOL_MINT, token_addr, trade_amount):
        update.message.reply_text(f"Bought {token_addr} with {trade_amount/1e9:.4f} SOL")
        current_trade['amount'] = trade_amount
    else:
        update.message.reply_text("Main buy failed.")
        with trade_lock:
            current_trade = None

def manual_sell(update, context):
    global current_trade
    with trade_lock:
        if not current_trade:
            update.message.reply_text("No active trade.")
            return
        token_addr = current_trade['token_address']
        amount_in = current_trade.get('amount')

    SOL_MINT = "So11111111111111111111111111111111111111112"
    if execute_swap(token_addr, SOL_MINT, int(amount_in)):
        update.message.reply_text(f"Sold {token_addr}. Trade closed.")
    else:
        update.message.reply_text(f"Sell transaction failed for {token_addr}")
    with trade_lock:
        current_trade = None

dispatcher.add_handler(CommandHandler('buy', manual_buy))
dispatcher.add_handler(CommandHandler('sell', manual_sell))

# ---------------- Scheduler ----------------
scheduler = BackgroundScheduler(timezone=pytz.UTC)
scheduler.add_job(scheduled_scan, 'interval', seconds=60)
scheduler.start()

# ---------------- Start Bot ----------------
updater.start_polling()
logger.info("Bot running...")
updater.idle()
