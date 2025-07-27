import os
import time
import threading
import base58
import base64
import requests

from solana.publickey import PublicKey
from solana.keypair import Keypair
from solana.rpc.api import Client
from solana.transaction import Transaction

from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# ===================== CONFIGURATION ===================== #
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Private key for the trading wallet (base58 encoded or array of ints as string)
PRIVATE_KEY_STR = os.getenv("SOL_PRIVATE_KEY")
# RPC endpoint for Solana network (default to mainnet-beta public RPC)
RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# Trading parameters
VOLUME_THRESHOLD = 50000.0        # minimum 24h volume in USD
PRICE_CHANGE_THRESHOLD = 2.0      # minimum price change in last 5 minutes (%)
MAX_CONCURRENT_TRADES = 3         # max number of parallel trades
TEST_TRADE_USD = 0.01             # small test buy amount in USD
MAIN_TRADE_USD = 5.0              # main trade amount in USD
SL_PERCENT = 0.10                 # stop-loss 10% below entry (as decimal 0.10)
TP_THRESHOLD = 0.15               # trigger trailing when price +15%
TRAILING_PERCENT = 0.10           # trail stop 10% below peak after TP trigger

# Scheduler intervals (in seconds)
SCAN_INTERVAL = 60     # how often to scan DexScreener for new tokens
CHECK_INTERVAL = 15    # how often to check open trades for SL/TP

# ===================== INITIALIZATION ===================== #

# Initialize Solana RPC client
client = Client(RPC_URL)

# Load the private key for the Solana wallet
if not PRIVATE_KEY_STR:
    raise RuntimeError("Private key not provided. Set SOL_PRIVATE_KEY in environment.")
try:
    # Try base58 decode (common format)
    secret_key = base58.b58decode(PRIVATE_KEY_STR)
except Exception:
    try:
        # If base58 fails, maybe it's a JSON array string of ints
        import json
        ints = json.loads(PRIVATE_KEY_STR)
        secret_key = bytes(ints)
    except Exception as e:
        raise RuntimeError("Failed to decode private key.") from e

trader_keypair = Keypair.from_secret_key(secret_key)
WALLET_ADDRESS = str(trader_keypair.public_key)

# Telegram bot setup
if not BOT_TOKEN:
    raise RuntimeError("Telegram BOT_TOKEN not provided in environment.")
updater = Updater(BOT_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# Chat ID for notifications (will be set when user sends /start or first command)
CHAT_ID = None

# Lock for thread-safe access to trades
trade_lock = threading.Lock()

# Trade data class
class Trade:
    def __init__(self, symbol: str, base_token: str, quote_token: str, pair_address: str,
                 entry_price: float, quantity: float):
        self.symbol = symbol                   # token symbol
        self.base_token = base_token           # base token address (token being traded)
        self.quote_token = quote_token         # quote token address (stablecoin used)
        self.pair_address = pair_address       # DEX pair address for price tracking
        self.entry_price = entry_price         # entry price in USD
        self.quantity = quantity               # total quantity of token bought
        self.current_sl = entry_price * (1 - SL_PERCENT)  # current stop-loss price
        self.reached_tp = False                # whether TP threshold (15% gain) reached
        self.peak_price = entry_price          # highest price since TP reached (for trailing)

# Dictionary to track open trades: key = base_token_address, value = Trade object
open_trades: dict[str, Trade] = {}

# ===================== DEXSCREENER SCANNING ===================== #
def scan_dexscreener():
    """Scan DexScreener API for Solana tokens meeting volume and price change criteria and open new trades."""
    global open_trades
    # If max trades running, skip scanning
    with trade_lock:
        if len(open_trades) >= MAX_CONCURRENT_TRADES:
            return

    try:
        # Queries for Solana DEXes (Raydium and Orca) to get token pairs
        queries = ["raydium solana", "orca solana"]
        results = []
        for query in queries:
            url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
            res = requests.get(url, timeout=10)
            data = res.json()
            if data and data.get("pairs"):
                results.extend(data["pairs"])
    except Exception as e:
        print(f"Error fetching DexScreener data: {e}")
        return

    # Filter and de-duplicate results
    seen_tokens = set()
    candidates = []
    for pair in results:
        # Base token info
        base_token_addr = pair.get('baseToken', {}).get('address')
        symbol = pair.get('baseToken', {}).get('symbol', '')
        quote_symbol = pair.get('quoteToken', {}).get('symbol', '')
        if not base_token_addr or not symbol:
            continue
        # Only consider tokens with stablecoin (USDC/USDT) as quote
        if quote_symbol not in ("USDC", "USDT"):
            continue
        # Skip if already trading this token
        with trade_lock:
            if base_token_addr in open_trades:
                continue
        # Skip duplicates in results
        if base_token_addr in seen_tokens:
            continue
        seen_tokens.add(base_token_addr)
        # Volume filter (24h volume)
        vol_info = pair.get('volume', {})
        vol_24h = None
        # Some DEXs might have volume under different keys (e.g., 'h24'), try common ones
        if 'h24' in vol_info:
            vol_24h = float(vol_info['h24'])
        elif '24h' in vol_info:
            vol_24h = float(vol_info['24h'])
        else:
            # If not provided, skip volume filtering
            vol_24h = None
        if vol_24h is None or vol_24h < VOLUME_THRESHOLD:
            continue
        # Price change filter (5m change)
        change_info = pair.get('priceChange', {})
        change_5m = None
        # Try keys 'm5' or '5m'
        if 'm5' in change_info:
            change_5m = float(change_info['m5'])
        elif '5m' in change_info:
            change_5m = float(change_info['5m'])
        if change_5m is None or change_5m < PRICE_CHANGE_THRESHOLD:
            continue
        # Candidate passes filters
        candidates.append(pair)

    if not candidates:
        return

    # Sort candidates by volume (descending) to prioritize higher volume tokens
    candidates.sort(key=lambda p: float(p.get('volume', {}).get('h24', 0) or 0), reverse=True)

    # Try to open trades for each candidate until limits are reached
    for pair in candidates:
        with trade_lock:
            if len(open_trades) >= MAX_CONCURRENT_TRADES:
                break  # reached max parallel trades
        base_token_addr = pair['baseToken']['address']
        symbol = pair['baseToken']['symbol']
        quote_token_addr = pair['quoteToken']['address']
        quote_symbol = pair['quoteToken']['symbol']
        pair_address = pair.get('pairAddress')
        dex_name = pair.get('dexId')

        # Double-check not already trading this token (in case it was added by a parallel thread)
        with trade_lock:
            if base_token_addr in open_trades:
                continue

        # Prepare Jupiter swap parameters
        # Input is the quote (stablecoin), output is the base token
        input_mint = quote_token_addr
        output_mint = base_token_addr
        # Calculate amount in smallest units (assume stablecoin has 6 decimals)
        test_amount_minor = int(TEST_TRADE_USD * (10**6))
        main_amount_minor = int(MAIN_TRADE_USD * (10**6))

        # Small test buy to detect non-tradeable tokens (spam/honeypot)
        test_success = False
        try:
            quote_url = (f"https://quote-api.jup.ag/v4/quote?inputMint={input_mint}"
                         f"&outputMint={output_mint}&amount={test_amount_minor}&slippageBps=500")
            quote_resp = requests.get(quote_url, timeout=5).json()
            if not quote_resp.get("data"):
                raise Exception("No route for test swap")
            route = quote_resp["data"][0]
            swap_req = {
                "route": route,
                "userPublicKey": WALLET_ADDRESS,
                "wrapAndUnwrapSol": False
            }
            swap_resp = requests.post("https://quote-api.jup.ag/v4/swap", json=swap_req, timeout=5).json()
            swap_tx = swap_resp.get("swapTransaction")
            if not swap_tx:
                raise Exception("Failed to get swapTransaction for test")
            # Deserialize, sign and send transaction
            tx = Transaction.deserialize(base64.b64decode(swap_tx))
            client.send_transaction(tx, trader_keypair)
            test_success = True
        except Exception as e:
            print(f"[{symbol}] Test buy failed: {e}")

        if not test_success:
            continue  # skip this token if test buy failed

        # Execute main buy order
        try:
            quote_url = (f"https://quote-api.jup.ag/v4/quote?inputMint={input_mint}"
                         f"&outputMint={output_mint}&amount={main_amount_minor}&slippageBps=500")
            quote_resp = requests.get(quote_url, timeout=5).json()
            if not quote_resp.get("data"):
                raise Exception("No route for main swap")
            route = quote_resp["data"][0]
            swap_req = {
                "route": route,
                "userPublicKey": WALLET_ADDRESS,
                "wrapAndUnwrapSol": False
            }
            swap_resp = requests.post("https://quote-api.jup.ag/v4/swap", json=swap_req, timeout=5).json()
            swap_tx = swap_resp.get("swapTransaction")
            if not swap_tx:
                raise Exception("Failed to get swapTransaction for main swap")
            tx = Transaction.deserialize(base64.b64decode(swap_tx))
            client.send_transaction(tx, trader_keypair)
        except Exception as e:
            print(f"[{symbol}] Main buy failed: {e}")
            continue

        # Determine entry price (approximate). Use provided priceUsd, or fetch pair info if missing.
        entry_price = 0.0
        try:
            entry_price = float(pair.get('priceUsd', 0))
        except:
            entry_price = 0.0
        if entry_price <= 0:
            # Fetch latest price from pair endpoint
            try:
                p_info = requests.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}",
                                      timeout=5).json()
                if p_info and p_info.get("pairs"):
                    entry_price = float(p_info["pairs"][0].get("priceUsd", 0) or 0)
            except:
                entry_price = 0.0
        if entry_price <= 0:
            print(f"[{symbol}] Could not retrieve entry price, skipping trade.")
            continue

        # Estimate total quantity bought (including test trade) based on entry price
        total_spent = TEST_TRADE_USD + MAIN_TRADE_USD
        quantity = total_spent / entry_price

        # Create Trade object and add to open_trades
        trade = Trade(symbol, base_token_addr, quote_token_addr, pair_address, entry_price, quantity)
        with trade_lock:
            open_trades[base_token_addr] = trade
        # Optionally, notify that a new trade opened (not required by spec, so omitted)
        print(f"Opened trade: {symbol} | Entry: ${entry_price:.6f} | Qty: {quantity:.4f}")

# ===================== TRADE MANAGEMENT (SL/TP) ===================== #
def check_trades():
    """Check open trades for stop-loss or take-profit conditions and update or close positions."""
    global open_trades, CHAT_ID
    token_addresses = []
    with trade_lock:
        token_addresses = list(open_trades.keys())
    if not token_addresses:
        return

    # Get current prices for all open trade pairs in one API call (comma-separated pair addresses)
    pair_addresses = []
    with trade_lock:
        for t_addr, trade in open_trades.items():
            pair_addresses.append(trade.pair_address)
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{','.join(pair_addresses)}"
        resp = requests.get(url, timeout=5).json()
        pair_data_list = resp.get("pairs", [])
    except Exception as e:
        print(f"Error fetching current prices: {e}")
        return

    # Build a map from pairAddress to current price
    current_prices = {}
    for p in pair_data_list:
        try:
            p_addr = p.get("pairAddress")
            price = float(p.get("priceUsd", 0) or 0)
            if p_addr:
                current_prices[p_addr] = price
        except:
            continue

    for token_addr in token_addresses:
        trade = None
        with trade_lock:
            trade = open_trades.get(token_addr)
        if not trade:
            continue  # trade might have been removed by another thread
        price = current_prices.get(trade.pair_address)
        if price is None or price <= 0:
            continue

        # Check stop-loss trigger
        if price <= trade.current_sl:
            # Price fell to or below stop-loss -> close the trade
            profit_usd = (price - trade.entry_price) * trade.quantity
            profit_pct = (price - trade.entry_price) / trade.entry_price * 100
            # Remove trade from open_trades
            with trade_lock:
                open_trades.pop(token_addr, None)
            # Execute sell to stablecoin
            try:
                # Determine how much token to sell (query actual balance for accuracy)
                sell_amount = trade.quantity
                token_pub = PublicKey(trade.base_token)
                # Find token account for this token
                resp = client.get_token_accounts_by_owner(PublicKey(WALLET_ADDRESS),
                                                         solana.rpc.types.TokenAccountOpts(mint=token_pub))
                token_accounts = resp.get('result', {}).get('value', [])
                if token_accounts:
                    token_account_pubkey = token_accounts[0]['pubkey']
                    balance_info = client.get_token_account_balance(PublicKey(token_account_pubkey))
                    if balance_info.get('result') and balance_info['result'].get('value'):
                        ui_amount = balance_info['result']['value'].get('uiAmount')
                        if ui_amount is not None:
                            sell_amount = float(ui_amount)
                            # Update quantity to actual if needed
                            trade.quantity = sell_amount
                # Convert sell amount to minor units based on decimals
                decimals = 0
                if balance_info and balance_info['result']['value'].get('decimals') is not None:
                    decimals = int(balance_info['result']['value']['decimals'])
                else:
                    decimals = 6  # assume 6 if unknown
                sell_amount_minor = int(sell_amount * (10 ** decimals))
                # Swap token to stable (same stable as originally used)
                input_mint = trade.base_token      # token we have
                output_mint = trade.quote_token    # stable we want back
                quote_url = (f"https://quote-api.jup.ag/v4/quote?inputMint={input_mint}"
                             f"&outputMint={output_mint}&amount={sell_amount_minor}&slippageBps=500")
                quote_resp = requests.get(quote_url, timeout=5).json()
                if quote_resp.get("data"):
                    route = quote_resp["data"][0]
                    swap_req = {
                        "route": route,
                        "userPublicKey": WALLET_ADDRESS,
                        "wrapAndUnwrapSol": False
                    }
                    swap_resp = requests.post("https://quote-api.jup.ag/v4/swap", json=swap_req, timeout=5).json()
                    swap_tx = swap_resp.get("swapTransaction")
                    if swap_tx:
                        tx = Transaction.deserialize(base64.b64decode(swap_tx))
                        client.send_transaction(tx, trader_keypair)
            except Exception as e:
                print(f"[{trade.symbol}] Error during sell: {e}")

            # Send P&L notification to Telegram
            if CHAT_ID:
                result_text = "lãi" if profit_usd >= 0 else "lỗ"
                message = (f"Đóng lệnh {trade.symbol} tại ${price:.6f} -> PNL: "
                           f"{profit_usd:+.2f} USD ({profit_pct:+.2f}%) ({result_text})")
                updater.bot.send_message(chat_id=CHAT_ID, text=message)
            print(f"Closed trade: {trade.symbol} at ${price:.6f}, P/L={profit_usd:.2f} USD ({profit_pct:.2f}%)")
            # Move to next trade after closing
            continue

        # If not stopped out, check if take-profit threshold reached
        if not trade.reached_tp and price >= trade.entry_price * (1 + TP_THRESHOLD):
            # Price has increased ≥15% from entry -> activate trailing stop (move SL to entry)
            trade.reached_tp = True
            trade.current_sl = trade.entry_price  # move stop-loss to breakeven
            trade.peak_price = price
            # Notify trailing start (optional)
            if CHAT_ID:
                msg = f"{trade.symbol} tăng >= {TP_THRESHOLD*100:.0f}%, dời SL về điểm vào."
                updater.bot.send_message(chat_id=CHAT_ID, text=msg)
            print(f"Trailing stop activated for {trade.symbol}. SL set to ${trade.current_sl:.6f}")

        # If trailing is active, update trailing stop based on new peak price
        if trade.reached_tp:
            if price > trade.peak_price:
                # New peak price
                trade.peak_price = price
                # Update trailing stop 10% below new peak
                trade.current_sl = trade.peak_price * (1 - TRAILING_PERCENT)
                print(f"{trade.symbol}: New peak ${price:.6f}, update SL to ${trade.current_sl:.6f}")

# ===================== TELEGRAM COMMAND HANDLERS ===================== #
def start_command(update: Update, context: CallbackContext) -> None:
    """Handle /start command - greets user and store chat_id for notifications."""
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    update.message.reply_text("Bot giao dịch Solana đã kích hoạt. Sử dụng /closetrade <token> để đóng lệnh thủ công.")

def closetrade_command(update: Update, context: CallbackContext) -> None:
    """Handle /closetrade command - close an open trade manually."""
    global open_trades, CHAT_ID
    CHAT_ID = update.effective_chat.id  # update chat id in case
    args = context.args
    if len(args) == 0:
        # No token specified
        with trade_lock:
            if not open_trades:
                update.message.reply_text("Hiện không có lệnh nào đang mở.")
            elif len(open_trades) == 1:
                # Only one trade open, close it
                token_addr, trade = next(iter(open_trades.items()))
                token = trade.symbol
                update.message.reply_text(f"Đang đóng lệnh {token} theo yêu cầu...")
                # Simulate price at current for closing
                current_price = None
                try:
                    p_info = requests.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{trade.pair_address}",
                                          timeout=5).json()
                    if p_info and p_info.get("pairs"):
                        current_price = float(p_info["pairs"][0].get("priceUsd", 0) or 0)
                except:
                    current_price = None
                if current_price is None or current_price <= 0:
                    current_price = trade.entry_price  # fallback to entry if price fetch fails
                # Manually trigger stop-loss at current price
                trade.current_sl = current_price + 1e-9  # set SL just above current to trigger closure in check_trades
                # (We'll rely on the next check_trades cycle to actually close it, to reuse logic)
                update.message.reply_text(f"Lệnh {token} sẽ được đóng ở giá ~${current_price:.6f}.")
            else:
                # Multiple trades open, ask user to specify
                tokens = [t.symbol for t in open_trades.values()]
                update.message.reply_text("Vui lòng chỉ định token cần đóng. Đang mở: " + ", ".join(tokens))
        return

    # If token symbol provided
    token_symbol = args[0].upper()
    to_close = None
    with trade_lock:
        for t_addr, tr in open_trades.items():
            if tr.symbol.upper() == token_symbol:
                to_close = tr
                break
    if not to_close:
        update.message.reply_text(f"Không tìm thấy lệnh với token {token_symbol}.")
    else:
        update.message.reply_text(f"Đang đóng lệnh {to_close.symbol} theo yêu cầu...")
        # Fetch current price and trigger closure
        current_price = None
        try:
            p_info = requests.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{to_close.pair_address}",
                                  timeout=5).json()
            if p_info and p_info.get("pairs"):
                current_price = float(p_info["pairs"][0].get("priceUsd", 0) or 0)
        except:
            current_price = None
        if current_price is None or current_price <= 0:
            current_price = to_close.entry_price
        # Adjust SL to trigger closing in check_trades
        to_close.current_sl = current_price + 1e-9
        update.message.reply_text(f"Lệnh {to_close.symbol} sẽ được đóng ở giá ~${current_price:.6f}.")

# Register Telegram handlers
dispatcher.add_handler(CommandHandler("start", start_command))
dispatcher.add_handler(CommandHandler("closetrade", closetrade_command, pass_args=True))

# ===================== SCHEDULER SETUP ===================== #
from apscheduler.schedulers.background import BackgroundScheduler
scheduler = BackgroundScheduler()
scheduler.add_job(scan_dexscreener, 'interval', seconds=SCAN_INTERVAL, id='scan_job')
scheduler.add_job(check_trades, 'interval', seconds=CHECK_INTERVAL, id='check_job')
scheduler.start()

# Start polling for Telegram commands
updater.start_polling(drop_pending_updates=True)
print("Trading bot is running. Press Ctrl+C to stop.")
updater.idle()
