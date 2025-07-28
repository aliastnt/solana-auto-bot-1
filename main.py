import os
import base58
import json
import requests
import threading
from datetime import datetime

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from solana.rpc.api import Client
from solana.rpc.types import TxOpts, TokenAccountOpts

# ================== Configuration ===================
# Environment variables (for Railway deployment)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # base58 encoded private key (64 bytes)
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# DexScreener API endpoints
DEX_API_BASE = "https://api.dexscreener.com"
DEX_SEARCH_ENDPOINT = f"{DEX_API_BASE}/latest/dex/search"
DEX_PAIRS_ENDPOINT = f"{DEX_API_BASE}/latest/dex/pairs"

# Jupiter Aggregator API endpoints
JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_API = "https://quote-api.jup.ag/v6/swap"

# Solana token mints
SOL_MINT = "So11111111111111111111111111111111111111112"  # Wrapped SOL (SOL)
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC on Solana

# Trade parameters
TEST_TRADE_USD = 0.01       # test trade amount in USD
MAIN_TRADE_PORTION = 0.3    # use 30% of current SOL balance for main trade
SLIPPAGE_TEST_BPS = 1000    # 10% slippage tolerance for test trades (in bps)
SLIPPAGE_MAIN_BPS = 500     # 5% slippage tolerance for main trades (in bps)

# Global state variables
wallet = None               # Keypair for the trading wallet
solana_client = None        # Solana RPC client
bot = None                  # Telegram bot instance (set after initialization)
CHAT_ID = None              # Telegram chat ID for notifications (set on /start)
open_trades = []            # List of dicts for active positions
open_trades_lock = threading.Lock()

# =============== Wallet and Solana Setup ===============
def load_or_generate_wallet():
    """Load wallet from PRIVATE_KEY or generate new wallet if not provided."""
    global wallet
    if PRIVATE_KEY:
        try:
            # Decode base58 secret key (expected 64 bytes for Phantom export)
            secret_key_bytes = base58.b58decode(PRIVATE_KEY.strip())
            wallet = Keypair.from_bytes(secret_key_bytes)
        except Exception as e:
            print(f"Failed to load wallet from private key: {e}")
            wallet = Keypair()  # fallback to new wallet
    else:
        wallet = Keypair()
    pubkey = wallet.pubkey()
    print(f"Using wallet {pubkey}")
    return pubkey

# Initialize wallet and Solana client
wallet_pubkey = load_or_generate_wallet()
solana_client = Client(RPC_URL)

# =============== Helper Functions ===============
def get_solana_balance():
    """Get current SOL balance of the wallet (in SOL)."""
    try:
        balance_lamports = solana_client.get_balance(wallet.pubkey())['result']['value']
        return balance_lamports / 1e9
    except Exception as e:
        print(f"Error getting SOL balance: {e}")
        return 0.0

def fetch_sol_price_usd():
    """Fetch current SOL price in USD using DexScreener (SOL/USDC pair)."""
    try:
        params = {"q": "SOL/USDC"}
        resp = requests.get(DEX_SEARCH_ENDPOINT, params=params, timeout=10)
        data = resp.json()
        if 'pairs' in data and data['pairs']:
            for pair in data['pairs']:
                if pair.get('chainId') == 'solana':
                    price_usd = float(pair.get('priceUsd', 0) or 0)
                    if price_usd > 0:
                        return price_usd
        return None
    except Exception as e:
        print(f"Error fetching SOL price: {e}")
        return None

def scan_tokens():
    """Scan DexScreener for tokens meeting criteria and initiate trades."""
    # Only proceed if capacity for new trades exists
    with open_trades_lock:
        if len(open_trades) >= 3:
            return  # already at max 3 concurrent trades
    try:
        # Search Solana tokens paired with USDC
        resp_usdc = requests.get(DEX_SEARCH_ENDPOINT, params={"q": "solana USDC"}, timeout=10)
        data_usdc = resp_usdc.json()
        pairs = []
        if 'pairs' in data_usdc:
            for p in data_usdc['pairs']:
                if p.get('chainId') == 'solana':
                    pairs.append(p)
        # Search Solana tokens paired with SOL
        resp_sol = requests.get(DEX_SEARCH_ENDPOINT, params={"q": "solana SOL"}, timeout=10)
        data_sol = resp_sol.json()
        if 'pairs' in data_sol:
            for p in data_sol['pairs']:
                if p.get('chainId') == 'solana':
                    # avoid duplicates
                    if p.get('pairAddress') not in [x.get('pairAddress') for x in pairs]:
                        pairs.append(p)
        # Filter tokens: volume >= 50k USD and price change >= 2% (5m)
        candidates = []
        for pair in pairs:
            vol = 0.0
            pc_5m = 0.0
            if pair.get('volume') and 'h24' in pair['volume']:
                try:
                    vol = float(pair['volume']['h24'])
                except:
                    vol = 0.0
            if pair.get('priceChange') and 'm5' in pair['priceChange']:
                try:
                    pc_5m = float(pair['priceChange']['m5'])
                except:
                    pc_5m = 0.0
            if vol >= 50000 and pc_5m >= 2:
                candidates.append(pair)
        # Attempt trades on each candidate until max positions reached
        for pair in candidates:
            with open_trades_lock:
                if len(open_trades) >= 3:
                    break
                # skip if already have a position for this token
                token_addr = pair.get('baseToken', {}).get('address')
                if any(tr['token_address'] == token_addr for tr in open_trades):
                    continue
            base_symbol = pair.get('baseToken', {}).get('symbol', '')
            token_addr = pair.get('baseToken', {}).get('address')
            # Spam check: test buy and sell
            success = test_token_trade(token_addr)
            if not success:
                continue  # skip token if test trade failed
            # Execute main trade
            main_trade = execute_main_trade(token_addr)
            if main_trade:
                position = {
                    "token_address": token_addr,
                    "token_symbol": base_symbol,
                    "pair_address": pair.get('pairAddress', ''),
                    "entry_price_usd": main_trade['entry_price_usd'],
                    "quantity": main_trade['quantity'],
                    "spent_sol": main_trade['spent_sol'],
                    "stop_loss_price": main_trade['entry_price_usd'] * 0.9,
                    "trailing": False,
                    "peak_price_usd": main_trade['entry_price_usd']
                }
                with open_trades_lock:
                    open_trades.append(position)
                # Notify user about opened position
                if CHAT_ID and bot:
                    entry = position['entry_price_usd']
                    sl = position['stop_loss_price']
                    tp_trigger = entry * 1.15
                    bot.send_message(
                        CHAT_ID,
                        f"📈 Opened position: Buy {base_symbol} at ${entry:.6f}. "
                        f"Spent {main_trade['spent_sol']:.4f} SOL. "
                        f"SL @ ${sl:.6f}, trailing start @ ${tp_trigger:.6f}."
                    )
    except Exception as e:
        print(f"Error in scan_tokens: {e}")

def test_token_trade(token_mint):
    """Perform a small test buy (~$0.01) and sell to check if token is tradeable."""
    try:
        sol_price = fetch_sol_price_usd()
        if sol_price is None or sol_price <= 0:
            return False
        # Calculate SOL amount (lamports) equivalent to $0.01
        sol_needed = TEST_TRADE_USD / sol_price  # in SOL
        lamports = int(sol_needed * 1e9)
        if lamports < 1:
            lamports = 1
        # 1. Buy a small amount of token (SOL -> token)
        swap_resp = jupiter_swap(SOL_MINT, token_mint, lamports, SLIPPAGE_TEST_BPS)
        if not swap_resp or 'txid' not in swap_resp:
            return False
        # 2. Sell the token back to SOL
        owner_pubkey = wallet.pubkey()
        input_amount = 0
        balance = 0.0
        try:
            token_accounts = solana_client.get_token_accounts_by_owner(
                owner_pubkey,
                TokenAccountOpts(mint=Pubkey.from_string(token_mint), encoding="jsonParsed"),
                commitment="confirmed"
            )
            if token_accounts['result']['value']:
                acct = token_accounts['result']['value'][0]
                amount_str = acct['account']['data']['parsed']['info']['tokenAmount']['amount']
                decimals = int(acct['account']['data']['parsed']['info']['tokenAmount']['decimals'])
                input_amount = int(amount_str)
                if decimals > 0:
                    balance = input_amount / (10 ** decimals)
                else:
                    balance = float(input_amount)
        except Exception as e:
            print(f"Error getting test token balance: {e}")
        if balance == 0 or input_amount == 0:
            return False
        swap_resp2 = jupiter_swap(token_mint, SOL_MINT, input_amount, SLIPPAGE_TEST_BPS)
        if not swap_resp2 or 'txid' not in swap_resp2:
            return False
        # Test buy & sell succeeded
        return True
    except Exception as e:
        print(f"Error in test_token_trade: {e}")
        return False

def execute_main_trade(token_mint):
    """Execute the main buy trade (30% of SOL balance into token). Returns trade details or None."""
    try:
        balance_lamports = solana_client.get_balance(wallet.pubkey())['result']['value']
        spend_lamports = int(balance_lamports * MAIN_TRADE_PORTION)
        if spend_lamports < 1000:
            return None  # not enough balance
        swap_resp = jupiter_swap(SOL_MINT, token_mint, spend_lamports, SLIPPAGE_MAIN_BPS)
        if not swap_resp or 'txid' not in swap_resp:
            return None
        # Get token balance and entry price
        quantity = 0.0
        try:
            token_accounts = solana_client.get_token_accounts_by_owner(
                wallet.pubkey(),
                TokenAccountOpts(mint=Pubkey.from_string(token_mint), encoding="jsonParsed"),
                commitment="confirmed"
            )
            if token_accounts['result']['value']:
                acct = token_accounts['result']['value'][0]
                amount_str = acct['account']['data']['parsed']['info']['tokenAmount']['amount']
                decimals = int(acct['account']['data']['parsed']['info']['tokenAmount']['decimals'])
                token_int = int(amount_str)
                if decimals > 0:
                    quantity = token_int / (10 ** decimals)
                else:
                    quantity = float(token_int)
        except Exception as e:
            print(f"Error parsing token quantity: {e}")
        # Compute entry price in USD
        sol_price = fetch_sol_price_usd()
        spent_sol = spend_lamports / 1e9
        entry_price_usd = 0.0
        if sol_price and quantity > 0:
            usd_spent = spent_sol * sol_price
            entry_price_usd = usd_spent / quantity if quantity > 0 else 0.0
        return {
            "entry_price_usd": entry_price_usd,
            "quantity": quantity,
            "spent_sol": spent_sol
        }
    except Exception as e:
        print(f"Error in execute_main_trade: {e}")
        return None

def jupiter_swap(input_mint, output_mint, amount, slippage_bps):
    """Perform a swap via Jupiter aggregator. Returns {'txid': ...} on success or None on failure."""
    try:
        # 1. Get quote for the swap
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps)
        }
        quote_resp = requests.get(JUPITER_QUOTE_API, params=params, timeout=10)
        quote = quote_resp.json()
        if not quote or "data" not in quote or len(quote["data"]) == 0:
            return None
        route = quote["data"][0]
        # 2. Request swap transaction from Jupiter
        swap_req = {
            "quoteResponse": route,
            "userPublicKey": str(wallet.pubkey()),
            "wrapAndUnwrapSol": True
        }
        swap_resp = requests.post(JUPITER_SWAP_API, json=swap_req, timeout=10)
        swap_data = swap_resp.json()
        if not swap_data or "swapTransaction" not in swap_data:
            return None
        swap_tx_base64 = swap_data["swapTransaction"]
        # 3. Deserialize, sign, and send the transaction
        # Decode base64 transaction
        try:
            swap_tx_bytes = json.loads(swap_tx_base64)  # in case returned in JSON form (unlikely)
        except:
            # If not JSON, treat as base64 string
            swap_tx_bytes = swap_tx_base64
        if isinstance(swap_tx_bytes, str):
            # If provided as base64 string
            if swap_tx_bytes.startswith("base58"):
                # If string is prefixed with "base58" (not expected), decode accordingly
                swap_tx_bytes = base58.b58decode(swap_tx_bytes.split("base58")[-1])
            else:
                swap_tx_bytes = base64.b64decode(swap_tx_bytes)
        tx = VersionedTransaction.from_bytes(swap_tx_bytes)
        # Sign transaction with our wallet keypair
        signature = wallet.sign_message(bytes(tx.message))
        signed_tx = VersionedTransaction.populate(tx.message, [signature])
        # Send the signed transaction
        raw_tx = bytes(signed_tx)
        send_opts = TxOpts(skip_preflight=True, preflight_commitment="confirmed", skip_confirmation=False)
        resp = solana_client.send_raw_transaction(raw_tx, opts=send_opts)
        if resp.get("result"):
            txid = resp["result"]
        elif resp.get("error"):
            return None
        else:
            txid = resp.get("result")
        return {"txid": txid}
    except Exception as e:
        print(f"Error in jupiter_swap: {e}")
        return None

def check_open_positions():
    """Check open positions for SL or TP conditions and close positions if needed."""
    try:
        # Make a shallow copy of open_trades to iterate (holds references to dicts)
        open_trades_copy = []
        with open_trades_lock:
            for pos in open_trades:
                open_trades_copy.append(pos)
        for pos in open_trades_copy:
            token_addr = pos['token_address']
            pair_addr = pos['pair_address']
            entry_price = pos['entry_price_usd']
            trailing_active = pos['trailing']
            peak_price = pos['peak_price_usd']
            current_price = 0.0
            # Fetch current price from DexScreener
            try:
                resp = requests.get(f"{DEX_PAIRS_ENDPOINT}/solana/{pair_addr}", timeout=5)
                data = resp.json()
                if data and 'pairs' in data and len(data['pairs']) > 0:
                    price_str = data['pairs'][0].get('priceUsd')
                    if price_str:
                        current_price = float(price_str)
            except Exception as e:
                print(f"Error fetching price for {token_addr}: {e}")
                continue
            if current_price <= 0:
                continue
            sell_reason = None
            # Stop-loss condition
            if current_price <= entry_price * 0.9:
                sell_reason = "stop-loss"
            # Trailing stop logic
            if sell_reason is None:
                if not trailing_active and current_price >= entry_price * 1.15:
                    # Activate trailing stop
                    with open_trades_lock:
                        for p in open_trades:
                            if p['token_address'] == token_addr:
                                p['trailing'] = True
                                p['peak_price_usd'] = current_price
                    trailing_active = True
                    peak_price = current_price
                    if CHAT_ID and bot:
                        bot.send_message(CHAT_ID, f"🔔 Trailing stop activated for {pos['token_symbol']} (price +15%). SL moved to entry.")
                if trailing_active:
                    if current_price > peak_price:
                        with open_trades_lock:
                            for p in open_trades:
                                if p['token_address'] == token_addr:
                                    p['peak_price_usd'] = current_price
                                    # local peak_price updated
                                    peak_price = current_price
                        peak_price = current_price
                    # Trailing stop trigger
                    if current_price <= peak_price * 0.9:
                        sell_reason = "trailing-stop"
            # If any condition to sell
            if sell_reason:
                input_amount = 0
                try:
                    token_accounts = solana_client.get_token_accounts_by_owner(
                        wallet.pubkey(),
                        TokenAccountOpts(mint=Pubkey.from_string(token_addr), encoding="jsonParsed"),
                        commitment="confirmed"
                    )
                    if token_accounts['result']['value']:
                        acct = token_accounts['result']['value'][0]
                        amount_str = acct['account']['data']['parsed']['info']['tokenAmount']['amount']
                        input_amount = int(amount_str)
                except Exception as e:
                    print(f"Error fetching balance for closing {token_addr}: {e}")
                if input_amount > 0:
                    # Check position still open (not closed manually in the meantime)
                    still_open = False
                    with open_trades_lock:
                        for p in open_trades:
                            if p['token_address'] == token_addr:
                                still_open = True
                                break
                    if not still_open:
                        continue
                    swap_resp = jupiter_swap(token_addr, SOL_MINT, input_amount, SLIPPAGE_MAIN_BPS)
                    if swap_resp and 'txid' in swap_resp:
                        with open_trades_lock:
                            open_trades[:] = [p for p in open_trades if p['token_address'] != token_addr]
                        pnl_percent = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
                        pnl_prefix = "▲" if pnl_percent >= 0 else "▼"
                        if CHAT_ID and bot:
                            bot.send_message(
                                CHAT_ID,
                                f"❌ Closed {pos['token_symbol']} at ${current_price:.6f} ({sell_reason}). "
                                f"PNL: {pnl_prefix}{pnl_percent:.2f}%"
                            )
    except Exception as e:
        print(f"Error in check_open_positions: {e}")

# =============== Telegram Bot Handlers ===============
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

def start_command(update: Update, context: CallbackContext):
    """Handle /start command."""
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    user = update.effective_user.first_name if update.effective_user else "there"
    update.message.reply_text(
        f"👋 Hi {user}, the trading bot is running.\n"
        f"Wallet address: {wallet.pubkey()}\n"
        f"Use /buy <token>, /sell <token>, /close <token> to trade."
    )

def buy_command(update: Update, context: CallbackContext):
    """Handle /buy command to manually buy a token."""
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    if len(context.args) == 0:
        update.message.reply_text("Usage: /buy <token_symbol_or_address>")
        return
    token_arg = context.args[0]
    # Determine token mint address from symbol or address
    token_mint = None
    if len(token_arg) >= 32:
        # Likely provided a mint address
        token_mint = token_arg
    else:
        # Search DexScreener for the symbol
        try:
            resp = requests.get(DEX_SEARCH_ENDPOINT, params={"q": token_arg}, timeout=5)
            data = resp.json()
            if 'pairs' in data:
                for p in data['pairs']:
                    if p.get('chainId') == 'solana':
                        token_mint = p.get('baseToken', {}).get('address')
                        break
        except Exception as e:
            token_mint = None
    if not token_mint:
        update.message.reply_text(f"Token {token_arg} not found on Solana.")
        return
    update.message.reply_text(f"⏳ Buying {token_arg}...")
    success = test_token_trade(token_mint)
    if not success:
        update.message.reply_text(f"❗ Unable to trade {token_arg} (test swap failed).")
        return
    trade = execute_main_trade(token_mint)
    if not trade:
        update.message.reply_text(f"❗ Failed to execute buy for {token_arg}.")
        return
    # Identify token symbol and pair address (for tracking)
    token_symbol = token_arg
    pair_address = ""
    try:
        resp = requests.get(DEX_SEARCH_ENDPOINT, params={"q": token_mint}, timeout=5)
        data = resp.json()
        if 'pairs' in data and data['pairs']:
            token_symbol = data['pairs'][0].get('baseToken', {}).get('symbol', token_arg)
            pair_address = data['pairs'][0].get('pairAddress', '')
    except:
        pair_address = ""
    position = {
        "token_address": token_mint,
        "token_symbol": token_symbol,
        "pair_address": pair_address,
        "entry_price_usd": trade['entry_price_usd'],
        "quantity": trade['quantity'],
        "spent_sol": trade['spent_sol'],
        "stop_loss_price": trade['entry_price_usd'] * 0.9,
        "trailing": False,
        "peak_price_usd": trade['entry_price_usd']
    }
    with open_trades_lock:
        open_trades.append(position)
    update.message.reply_text(
        f"✅ Bought {token_symbol} at ${trade['entry_price_usd']:.6f}. "
        f"SL set at ${trade['entry_price_usd']*0.9:.6f}."
    )

def sell_command(update: Update, context: CallbackContext):
    """Handle /sell command to manually sell a token (close position or sell holdings)."""
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    if len(context.args) == 0:
        update.message.reply_text("Usage: /sell <token_symbol_or_address>")
        return
    token_arg = context.args[0]
    token_mint = None
    if len(token_arg) >= 32:
        token_mint = token_arg
    else:
        # Find by symbol in open positions first
        with open_trades_lock:
            for pos in open_trades:
                if pos['token_symbol'].lower() == token_arg.lower():
                    token_mint = pos['token_address']
                    break
        # If not found, search DexScreener
        if not token_mint:
            try:
                resp = requests.get(DEX_SEARCH_ENDPOINT, params={"q": token_arg}, timeout=5)
                data = resp.json()
                if 'pairs' in data:
                    for p in data['pairs']:
                        if p.get('chainId') == 'solana':
                            token_mint = p.get('baseToken', {}).get('address')
                            break
            except:
                token_mint = None
    if not token_mint:
        update.message.reply_text(f"Token {token_arg} not found.")
        return
    input_amount = 0
    try:
        token_accounts = solana_client.get_token_accounts_by_owner(
            wallet.pubkey(),
            TokenAccountOpts(mint=Pubkey.from_string(token_mint), encoding="jsonParsed"),
            commitment="confirmed"
        )
        if token_accounts['result']['value']:
            acct = token_accounts['result']['value'][0]
            amount_str = acct['account']['data']['parsed']['info']['tokenAmount']['amount']
            input_amount = int(amount_str)
    except Exception as e:
        update.message.reply_text(f"Error fetching {token_arg} balance: {e}")
        return
    if input_amount == 0:
        update.message.reply_text(f"No {token_arg} tokens available to sell.")
        return
    swap_resp = jupiter_swap(token_mint, SOL_MINT, input_amount, SLIPPAGE_MAIN_BPS)
    if not swap_resp or 'txid' not in swap_resp:
        update.message.reply_text("❗ Sell swap failed.")
        return
    # Remove from open_trades if it was a tracked position
    with open_trades_lock:
        open_trades[:] = [p for p in open_trades if p['token_address'] != token_mint]
    update.message.reply_text(f"✅ Sold {token_arg}.")

def close_command(update: Update, context: CallbackContext):
    """Handle /close command (alias for /sell to close a position)."""
    sell_command(update, context)

# =============== Main Bot Launch ===============
if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN is not set.")
    else:
        updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
        bot = updater.bot
        dp = updater.dispatcher
        # Register command handlers
        dp.add_handler(CommandHandler("start", start_command))
        dp.add_handler(CommandHandler("buy", buy_command))
        dp.add_handler(CommandHandler("sell", sell_command))
        dp.add_handler(CommandHandler("close", close_command))
        # Start polling Telegram updates
        updater.start_polling()
        print("Bot started. Waiting for commands...")
        # Schedule periodic jobs
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(scan_tokens, 'interval', minutes=1, id='scan_tokens_job')
        scheduler.add_job(check_open_positions, 'interval', seconds=30, id='check_positions_job')
        scheduler.start()
        # Keep the bot running
        updater.idle()
