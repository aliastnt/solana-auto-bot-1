# main.py - Solana Auto Trading Bot
# Import required libraries
import os
import logging
import base64
import asyncio
import threading
from telegram.ext import Updater, CommandHandler
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solana.transaction import Transaction
from solana.rpc.api import Client
import requests
import pytz
from apscheduler.schedulers.background import BackgroundScheduler

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
PRIVATE_KEY = os.getenv('PRIVATE_KEY')
RPC_URL = os.getenv('SOLANA_ENDPOINT')

# Validate critical environment variables
missing_vars = []
if not TELEGRAM_TOKEN: missing_vars.append('TELEGRAM_BOT_TOKEN')
if not CHAT_ID: missing_vars.append('CHAT_ID')
if not PRIVATE_KEY: missing_vars.append('PRIVATE_KEY')
if not RPC_URL: missing_vars.append('SOLANA_ENDPOINT')
if missing_vars:
    logger.error(f"Error: Missing environment variables: {', '.join(missing_vars)}")
    exit(1)

# Initialize Solana client and keypair
solana_client = Client(RPC_URL)
try:
    trader_keypair = Keypair.from_base58_string(PRIVATE_KEY)
except Exception as e:
    logger.error(f"Failed to load keypair from PRIVATE_KEY: {e}")
    exit(1)
wallet_address = str(trader_keypair.pubkey())
logger.info(f"Wallet address: {wallet_address}")

# Initialize Telegram bot
updater = Updater(TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# Global state for current trade
current_trade = None  # holds info of active trade (if any)
trade_lock = threading.Lock()

# Async function to fetch trending pairs from DexScreener (Solana only)
async def fetch_trending_pairs():
    import websockets
    uri = "wss://io.dexscreener.com/dex/screener/pairs/h24/1?chain=solana&rankBy[key]=trendingScoreM5&rankBy[order]=desc"
    async with websockets.connect(uri, ping_timeout=20, close_timeout=5) as websocket:
        message_raw = await websocket.recv()
        return message_raw

def get_trending_pairs():
    """Retrieve trending pairs data from DexScreener via websocket (returns list of pair dicts)."""
    try:
        data_str = asyncio.get_event_loop().run_until_complete(fetch_trending_pairs())
    except Exception as e:
        logger.error(f"Failed to fetch trending pairs: {e}")
        return []
    try:
        import json
        data = json.loads(data_str)
        if data.get("type") == "pairs":
            return data.get("pairs", [])
        else:
            return []
    except Exception as e:
        logger.error(f"Failed to parse trending data: {e}")
        return []

# Function to execute a token swap via Jupiter API
def execute_swap(input_mint, output_mint, amount_in, slippage_bps=50):
    """Perform a swap from input_mint to output_mint of the specified amount using Jupiter API."""
    # Jupiter quote API
    quote_url = (f"https://quote-api.jup.ag/v6/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount_in}&slippageBps={slippage_bps}")
    try:
        quote_resp = requests.get(quote_url)
        quote_data = quote_resp.json()
    except Exception as e:
        logger.error(f"Jupiter quote API error: {e}")
        return False
    if 'routePlan' in quote_data and len(quote_data['routePlan']) == 0:
        logger.info("No route found on Jupiter for swap.")
        return False
    # Jupiter swap API (request transaction)
    swap_req = {
        "quoteResponse": quote_data,
        "userPublicKey": wallet_address,
        "wrapAndUnwrapSol": True,
        "asLegacyTransaction": True
    }
    try:
        swap_resp = requests.post("https://quote-api.jup.ag/v6/swap", json=swap_req)
        swap_data = swap_resp.json()
    except Exception as e:
        logger.error(f"Jupiter swap API error: {e}")
        return False
    swap_tx_base64 = swap_data.get('swapTransaction')
    if not swap_tx_base64:
        logger.error("Jupiter swap API did not return a transaction.")
        return False
    # Decode and send transaction
    try:
        tx_bytes = base64.b64decode(swap_tx_base64)
        txn = Transaction.deserialize(tx_bytes)
    except Exception as e:
        logger.error(f"Failed to deserialize swap transaction: {e}")
        return False
    try:
        solana_client.send_transaction(txn, trader_keypair)
        return True
    except Exception as e:
        logger.error(f"Failed to send transaction: {e}")
        return False

# Scheduled job: scan trending pairs and auto-trade if conditions met
def scheduled_scan():
    global current_trade
    pairs = get_trending_pairs()
    if not pairs:
        return
    for pair in pairs:
        try:
            if pair.get('chainId') != 'solana':
                continue
            volume = pair.get('volume', {})
            price_change = pair.get('priceChange', {})
            vol_5m = float(volume.get('m5', 0) or 0)
            pc_5m = float(price_change.get('m5', 0) or 0)
            if vol_5m >= 50000 and pc_5m >= 2:
                token_addr = pair.get('baseToken', {}).get('address')
                token_symbol = pair.get('baseToken', {}).get('symbol', 'Unknown')
                with trade_lock:
                    if current_trade is not None:
                        # Already in a trade, skip new opportunities
                        continue
                    current_trade = {'token_address': token_addr, 'token_symbol': token_symbol}
                logger.info(f"Triggering trade for {token_symbol} - 5m Volume ${vol_5m:.0f}, PriceChange 5m {pc_5m:.2f}%")
                # Alert on Telegram about detected trade
                try:
                    updater.bot.send_message(chat_id=int(CHAT_ID),
                                              text=f"Detected {token_symbol} (address: {token_addr}) - Volume 5m ${vol_5m:.0f}, Price change 5m {pc_5m:.2f}%. Executing trade...")
                except Exception as e:
                    logger.error(f"Telegram notification failed: {e}")
                # Test buy 0.01 SOL
                SOL_MINT = "So11111111111111111111111111111111111111112"
                test_amount = int(0.01 * 1e9)  # 0.01 SOL in lamports
                if not execute_swap(SOL_MINT, token_addr, test_amount, slippage_bps=50):
                    logger.info("Test buy failed, aborting trade.")
                    try:
                        updater.bot.send_message(chat_id=int(CHAT_ID),
                                                  text=f"Test buy for {token_symbol} failed. Trade aborted.")
                    except: 
                        pass
                    with trade_lock:
                        current_trade = None
                    continue
                # Test sell 0.01 SOL worth of token
                if not execute_swap(token_addr, SOL_MINT, test_amount, slippage_bps=50):
                    logger.info("Test sell failed, aborting trade.")
                    try:
                        updater.bot.send_message(chat_id=int(CHAT_ID),
                                                  text=f"Test sell for {token_symbol} failed. Trade aborted.")
                    except: 
                        pass
                    with trade_lock:
                        current_trade = None
                    continue
                # Test trades succeeded, proceed with main buy (30% of SOL balance)
                try:
                    balance_res = solana_client.get_balance(Pubkey.from_string(wallet_address))
                    balance_lamports = balance_res.get('result', {}).get('value', 0)
                except Exception as e:
                    logger.error(f"Failed to get wallet balance: {e}")
                    balance_lamports = 0
                trade_amount = int(balance_lamports * 0.3)
                if trade_amount < 1000:
                    logger.info("Insufficient balance for main trade, aborting.")
                    try:
                        updater.bot.send_message(chat_id=int(CHAT_ID),
                                                  text=f"Insufficient balance for main trade on {token_symbol}. Trade aborted.")
                    except: 
                        pass
                    with trade_lock:
                        current_trade = None
                    continue
                if execute_swap(SOL_MINT, token_addr, trade_amount, slippage_bps=50):
                    logger.info(f"Main buy for {token_symbol} executed (amount: {trade_amount} lamports). Waiting for sell command.")
                    try:
                        updater.bot.send_message(chat_id=int(CHAT_ID),
                                                  text=f"Bought {token_symbol} using {trade_amount/1e9:.4f} SOL. Use /sell to take profit or stop loss.")
                    except: 
                        pass
                    current_trade['amount'] = trade_amount
                else:
                    logger.error("Main buy transaction failed.")
                    try:
                        updater.bot.send_message(chat_id=int(CHAT_ID),
                                                  text=f"Main buy for {token_symbol} failed. No trade executed.")
                    except: 
                        pass
                    with trade_lock:
                        current_trade = None
        except Exception as e:
            logger.error(f"Error in scheduled_scan: {e}")

# Manual command: /buy <token_address> [<percent_of_SOL_balance>]
def manual_buy(update, context):
    global current_trade
    args = context.args
    if len(args) == 0:
        update.message.reply_text("Usage: /buy <token_address> [percent_of_SOL_balance]")
        return
    token_addr = args[0]
    # Default to 30% if percent not provided
    percent = 30.0
    if len(args) >= 2:
        try:
            percent = float(args[1])
        except:
            update.message.reply_text("Invalid percentage value.")
            return
    with trade_lock:
        if current_trade is not None:
            update.message.reply_text("A trade is already in progress. Please finish or /sell it first.")
            return
        current_trade = {'token_address': token_addr, 'token_symbol': token_addr}
    update.message.reply_text(f"Initiating manual trade for {token_addr}. Testing with 0.01 SOL...")
    SOL_MINT = "So11111111111111111111111111111111111111112"
    test_amount = int(0.01 * 1e9)
    if not execute_swap(SOL_MINT, token_addr, test_amount, slippage_bps=50):
        update.message.reply_text("Test buy failed. Trade aborted.")
        with trade_lock:
            current_trade = None
        return
    if not execute_swap(token_addr, SOL_MINT, test_amount, slippage_bps=50):
        update.message.reply_text("Test sell failed. Trade aborted.")
        with trade_lock:
            current_trade = None
        return
    # Perform main buy with specified percentage
    try:
        balance_res = solana_client.get_balance(Pubkey.from_string(wallet_address))
        balance_lamports = balance_res.get('result', {}).get('value', 0)
    except Exception as e:
        logger.error(f"Failed to get wallet balance: {e}")
        balance_lamports = 0
    trade_amount = int(balance_lamports * (percent / 100.0))
    if trade_amount < 1000:
        update.message.reply_text("Insufficient balance for main trade. Aborting.")
        with trade_lock:
            current_trade = None
        return
    if execute_swap(SOL_MINT, token_addr, trade_amount, slippage_bps=50):
        update.message.reply_text(f"Bought token {token_addr} using {trade_amount/1e9:.4f} SOL.")
        current_trade['amount'] = trade_amount
    else:
        update.message.reply_text("Main buy failed. Trade not executed.")
        with trade_lock:
            current_trade = None

# Manual command: /sell to sell the current holding token back to SOL
def manual_sell(update, context):
    global current_trade
    with trade_lock:
        if current_trade is None:
            update.message.reply_text("No active trade to sell.")
            return
        token_addr = current_trade.get('token_address')
        token_symbol = current_trade.get('token_symbol', token_addr)
        amount_in = current_trade.get('amount')
    SOL_MINT = "So11111111111111111111111111111111111111112"
    if not token_addr or not amount_in:
        update.message.reply_text("Trade data incomplete, cannot execute sell.")
        with trade_lock:
            current_trade = None
        return
    update.message.reply_text(f"Selling {token_symbol} back to SOL...")
    if execute_swap(token_addr, SOL_MINT, int(amount_in), slippage_bps=50):
        update.message.reply_text(f"Sold {token_symbol}. Trade closed.")
    else:
        update.message.reply_text(f"Sell transaction for {token_symbol} failed.")
    with trade_lock:
        current_trade = None

# Register Telegram command handlers
dispatcher.add_handler(CommandHandler('buy', manual_buy))
dispatcher.add_handler(CommandHandler('sell', manual_sell))

# Start scheduled scanning (every 60 seconds)
scheduler = BackgroundScheduler(timezone=pytz.UTC)
scheduler.add_job(scheduled_scan, 'interval', seconds=60)
scheduler.start()

# Start the bot
updater.start_polling()
logger.info("Bot is running - scanning markets and listening for commands...")
updater.idle()
