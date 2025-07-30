# main.py - Solana Auto Trading Bot (REST API version)

import os
import logging
import base64
import threading
import requests
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from telegram.ext import Updater, CommandHandler
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.transaction import Transaction
from solana.rpc.api import Client

# ====================== Logging ======================
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====================== ENV Variables ======================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
PRIVATE_KEY = os.getenv('PRIVATE_KEY')
RPC_URL = os.getenv('SOLANA_ENDPOINT')

missing_vars = []
if not TELEGRAM_TOKEN: missing_vars.append('TELEGRAM_BOT_TOKEN')
if not CHAT_ID: missing_vars.append('CHAT_ID')
if not PRIVATE_KEY: missing_vars.append('PRIVATE_KEY')
if not RPC_URL: missing_vars.append('SOLANA_ENDPOINT')

if missing_vars:
    logger.error(f"Error: Missing environment variables: {', '.join(missing_vars)}")
    exit(1)

# ====================== Solana ======================
solana_client = Client(RPC_URL)
try:
    trader_keypair = Keypair.from_base58_string(PRIVATE_KEY)
except Exception as e:
    logger.error(f"Failed to load keypair: {e}")
    exit(1)

wallet_address = str(trader_keypair.pubkey())
logger.info(f"Wallet address: {wallet_address}")

# ====================== Telegram ======================
updater = Updater(TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# ====================== Trade State ======================
current_trade = None
trade_lock = threading.Lock()

# ====================== DexScreener REST API ======================
def get_trending_pairs():
    """
    Lấy top cặp token trending trên mạng Solana (REST API).
    Trả về list các pair.
    """
    url = "https://api.dexscreener.com/latest/dex/pairs/solana"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            logger.error(f"DexScreener API error: {resp.status_code}")
            return []
        data = resp.json()
        return data.get("pairs", [])
    except Exception as e:
        logger.error(f"Failed to fetch trending pairs: {e}")
        return []

# ====================== Swap function (Jupiter) ======================
def execute_swap(input_mint, output_mint, amount_in, slippage_bps=50):
    quote_url = (f"https://quote-api.jup.ag/v6/quote?inputMint={input_mint}"
                 f"&outputMint={output_mint}&amount={amount_in}&slippageBps={slippage_bps}")
    try:
        quote_data = requests.get(quote_url, timeout=10).json()
    except Exception as e:
        logger.error(f"Jupiter quote API error: {e}")
        return False

    swap_req = {
        "quoteResponse": quote_data,
        "userPublicKey": wallet_address,
        "wrapAndUnwrapSol": True,
        "asLegacyTransaction": True
    }
    try:
        swap_data = requests.post("https://quote-api.jup.ag/v6/swap", json=swap_req, timeout=10).json()
    except Exception as e:
        logger.error(f"Jupiter swap API error: {e}")
        return False

    swap_tx_base64 = swap_data.get('swapTransaction')
    if not swap_tx_base64:
        logger.error("No transaction returned by Jupiter swap API")
        return False

    try:
        txn = Transaction.deserialize(base64.b64decode(swap_tx_base64))
        solana_client.send_transaction(txn, trader_keypair)
        return True
    except Exception as e:
        logger.error(f"Failed to send transaction: {e}")
        return False

# ====================== Scheduled Auto-Trade ======================
def scheduled_scan():
    global current_trade
    pairs = get_trending_pairs()
    if not pairs:
        return
    for pair in pairs:
        try:
            volume_usd = float(pair.get('volume', {}).get('h24', 0))
            change_5m = float(pair.get('priceChange', {}).get('m5', 0))
            if volume_usd >= 50000 and change_5m >= 2:
                token_addr = pair.get('baseToken', {}).get('address')
                token_symbol = pair.get('baseToken', {}).get('symbol', 'Unknown')
                with trade_lock:
                    if current_trade is not None:
                        continue
                    current_trade = {'token_address': token_addr, 'token_symbol': token_symbol}

                updater.bot.send_message(chat_id=int(CHAT_ID),
                    text=f"Detected {token_symbol} (addr: {token_addr}) - Vol24h ${volume_usd:.0f}, Δ5m {change_5m:.2f}% -> Trading")

                SOL_MINT = "So11111111111111111111111111111111111111112"
                test_amount = int(0.01 * 1e9)
                if not execute_swap(SOL_MINT, token_addr, test_amount):
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                        text=f"Test buy for {token_symbol} failed. Aborting.")
                    with trade_lock: current_trade = None
                    continue
                if not execute_swap(token_addr, SOL_MINT, test_amount):
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                        text=f"Test sell for {token_symbol} failed. Aborting.")
                    with trade_lock: current_trade = None
                    continue

                balance = solana_client.get_balance(Pubkey.from_string(wallet_address))
                balance_lamports = balance.get('result', {}).get('value', 0)
                trade_amount = int(balance_lamports * 0.3)
                if trade_amount < 1000:
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                        text="Insufficient balance for main trade.")
                    with trade_lock: current_trade = None
                    continue

                if execute_swap(SOL_MINT, token_addr, trade_amount):
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                        text=f"Main buy {token_symbol} amount: {trade_amount/1e9:.4f} SOL. Use /sell to close trade.")
                    current_trade['amount'] = trade_amount
                else:
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                        text="Main buy failed.")
                    with trade_lock: current_trade = None
        except Exception as e:
            logger.error(f"Error in scheduled_scan: {e}")

# ====================== Manual Commands ======================
def manual_buy(update, context):
    global current_trade
    args = context.args
    if len(args) == 0:
        update.message.reply_text("Usage: /buy <token_address> [percent]")
        return
    token_addr = args[0]
    percent = 30.0
    if len(args) > 1:
        try: percent = float(args[1])
        except: update.message.reply_text("Invalid percent value."); return

    with trade_lock:
        if current_trade is not None:
            update.message.reply_text("Trade already active, use /sell first.")
            return
        current_trade = {'token_address': token_addr, 'token_symbol': token_addr}

    SOL_MINT = "So11111111111111111111111111111111111111112"
    test_amount = int(0.01 * 1e9)
    if not execute_swap(SOL_MINT, token_addr, test_amount) or not execute_swap(token_addr, SOL_MINT, test_amount):
        update.message.reply_text("Test trade failed. Aborting.")
        with trade_lock: current_trade = None
        return

    balance = solana_client.get_balance(Pubkey.from_string(wallet_address))
    balance_lamports = balance.get('result', {}).get('value', 0)
    trade_amount = int(balance_lamports * (percent/100.0))
    if trade_amount < 1000:
        update.message.reply_text("Insufficient balance. Aborting.")
        with trade_lock: current_trade = None
        return

    if execute_swap(SOL_MINT, token_addr, trade_amount):
        update.message.reply_text(f"Bought token {token_addr} amount: {trade_amount/1e9:.4f} SOL.")
        current_trade['amount'] = trade_amount
    else:
        update.message.reply_text("Main buy failed.")
        with trade_lock: current_trade = None

def manual_sell(update, context):
    global current_trade
    with trade_lock:
        if current_trade is None:
            update.message.reply_text("No active trade.")
            return
        token_addr = current_trade.get('token_address')
        token_symbol = current_trade.get('token_symbol')
        amount_in = current_trade.get('amount')

    SOL_MINT = "So11111111111111111111111111111111111111112"
    if execute_swap(token_addr, SOL_MINT, int(amount_in)):
        update.message.reply_text(f"Sold {token_symbol}. Trade closed.")
    else:
        update.message.reply_text(f"Sell {token_symbol} failed.")
    with trade_lock: current_trade = None

# ====================== Telegram Command Handlers ======================
dispatcher.add_handler(CommandHandler('buy', manual_buy))
dispatcher.add_handler(CommandHandler('sell', manual_sell))

# ====================== Scheduler ======================
scheduler = BackgroundScheduler(timezone=pytz.UTC)
scheduler.add_job(scheduled_scan, 'interval', seconds=60)
scheduler.start()

# ====================== Run Bot ======================
updater.start_polling()
logger.info("Bot is running...")
updater.idle()
