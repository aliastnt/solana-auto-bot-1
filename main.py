import os
import re
import base58
import requests
from datetime import datetime
from decimal import Decimal
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import utc
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solana.transaction import TransactionError
from solana.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

# ============================== Configuration ==============================

# Load configuration from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SOLANA_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY")  # base58 encoded 64-byte private key (or 32-byte seed in some cases)
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# Trading strategy parameters
MIN_VOLUME_USD = 50000        # minimum 24h volume in USD to consider
MIN_PRICE_CHANGE_5M = 2.0     # minimum price change in last 5 minutes (in %)
TEST_TRADE_USD = Decimal("0.01")  # test trade amount in USD
TRADE_PORTION = Decimal("0.3")    # fraction of available SOL to use for main trade (30%)
STOP_LOSS_PCT = Decimal("0.10")   # 10% stoploss
TRAIL_STEP_PCT = Decimal("0.10")  # 10% trailing stop step
TRAIL_START_PCT = Decimal("0.15") # start trailing when price +15% from entry

# DexScreener URLs
DEXSCREENER_GAINERS_URL = "https://dexscreener.com/gainers/solana"

# Initialize Solana RPC client
solana_client = Client(SOLANA_RPC_URL)

# Restore Solana keypair from the private key
if not SOLANA_PRIVATE_KEY:
    raise RuntimeError("Missing SOLANA_PRIVATE_KEY in environment")
try:
    # Decode base58 string to bytes
    secret_key_bytes = base58.b58decode(SOLANA_PRIVATE_KEY.strip())
    # Use Keypair.from_bytes to construct the keypair (handles 64-byte or 32-byte seeds)
    solana_keypair = Keypair.from_bytes(secret_key_bytes)
except Exception as e:
    raise RuntimeError(f"Failed to load Solana keypair from private key: {e}")
user_pubkey = solana_keypair.pubkey()  # solders Pubkey object for the user's public key
user_pubkey_str = base58.b58encode(bytes(user_pubkey)).decode('utf-8')  # base58 public key string

# Initialize Telegram bot
from telegram.ext import Updater, CommandHandler, Filters
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# Data structure to track open trades
class Trade:
    """Represents an open trade position."""
    def __init__(self, symbol: str, token_address: str, quote_symbol: str,
                 amount_token: Decimal, initial_sol: Decimal, entry_price_usd: Decimal):
        self.symbol = symbol              # token symbol
        self.token_address = token_address  # token mint address (base58)
        self.quote = quote_symbol         # quote currency symbol (e.g. SOL or USDC)
        self.amount_token = amount_token  # amount of token purchased
        self.initial_sol = initial_sol    # SOL spent on the main trade
        self.entry_price_usd = entry_price_usd  # entry price in USD (per token) for reference
        self.entry_time = datetime.now()
        # Stop-loss and trailing stop variables
        self.stop_loss_factor = Decimal(1) - STOP_LOSS_PCT  # factor of initial value at which to stop (e.g. 0.9 for -10%)
        self.trailing_active = False
        self.peak_value_factor = Decimal(1)  # highest ratio of current SOL value / initial SOL value seen
    
    def update_stoploss(self, current_value_factor: Decimal):
        """
        Update stop-loss and trailing stop thresholds based on current value factor (current SOL value / initial SOL).
        Returns True if the trade should be closed (price fell to stop-loss), otherwise False.
        """
        # Update peak value factor if current value is new peak
        if current_value_factor > self.peak_value_factor:
            self.peak_value_factor = current_value_factor
        # Activate trailing stop if not yet active and profit exceeds threshold
        if not self.trailing_active and current_value_factor >= (Decimal(1) + TRAIL_START_PCT):
            self.trailing_active = True
            # Move stop-loss to break-even (entry price) when trailing starts
            if self.stop_loss_factor < Decimal(1):
                self.stop_loss_factor = Decimal(1)
        # Adjust trailing stop (move up stop_loss_factor) if trailing is active
        if self.trailing_active:
            # Trailing stop at 10% below the peak value
            target_factor = self.peak_value_factor * (Decimal(1) - TRAIL_STEP_PCT)
            if target_factor > self.stop_loss_factor:
                self.stop_loss_factor = target_factor
        # Determine if price has hit the stop-loss threshold
        if current_value_factor <= self.stop_loss_factor:
            return True  # price dropped to or below stop loss -> should close trade
        return False

# Dictionary to hold current open trades, keyed by token symbol
open_trades = {}

# Synchronization lock for trade operations (to handle concurrency between scheduler and Telegram commands)
import threading
trade_lock = threading.Lock()

# ============================== Trading Functions ==============================

def fetch_trending_tokens():
    """Fetch and parse the Solana trending/gainers tokens from DexScreener. Returns a list of candidate token info dicts."""
    headers = {"User-Agent": "Mozilla/5.0"}  # Use a common User-Agent to avoid blocking
    try:
        response = requests.get(DEXSCREENER_GAINERS_URL, headers=headers, timeout=10)
    except Exception as e:
        print(f"[!] Error fetching DexScreener data: {e}")
        return []
    if response.status_code != 200:
        print(f"[!] Unexpected status code {response.status_code} from DexScreener")
        return []
    html_text = response.text
    
    # The page contains lines with format like:
    # # 1 <prefix> SYMBOL / QUOTE Name $ Price Age Txns $ Volume Makers 5M% 1H% 6H% 24H% $ Liquidity $ MCAP
    # We will use regex to capture required fields for each row.
    # We'll capture: rank, optional prefix, token symbol, quote symbol, token name, price, volume, 5m change.
    pattern = re.compile(
        r'#\s*(\d+)\s*([A-Z0-9]+ )?([A-Z0-9]+)\s*/\s*([A-Z0-9]+)\s+([^$]+)\$?\s*([0-9.]+)\s*'  # rank, prefix (optional), token, quote, name, price
        r'[^$]*\$?\s*([0-9.]+[KM]?)\s*'   # volume (with K or M suffix possibly)
        r'[^%]*\s*([-+]?[0-9.]+)%\s*'     # 5M change
    )
    candidates = []
    for match in pattern.finditer(html_text):
        rank = int(match.group(1))
        prefix = match.group(2)  # may be None
        symbol = match.group(3)
        quote = match.group(4)
        name = match.group(5).strip()
        price_str = match.group(6)
        volume_str = match.group(7)
        change5m_str = match.group(8)
        try:
            price = Decimal(price_str)
        except:
            # If price parsing fails, skip this entry
            continue
        # Parse volume string with possible K/M suffix
        volume = None
        if volume_str.endswith("M"):
            # e.g. "3.8M" -> 3.8e6
            volume = float(volume_str[:-1]) * 1_000_000
        elif volume_str.endswith("K"):
            volume = float(volume_str[:-1]) * 1_000
        else:
            # no suffix, assume it's an integer string
            try:
                volume = float(volume_str.replace(",", ""))
            except:
                volume = None
        if volume is None:
            continue
        change5m = float(change5m_str)
        # Filter by volume and price change criteria
        if volume >= MIN_VOLUME_USD and change5m >= MIN_PRICE_CHANGE_5M:
            candidates.append({
                "symbol": symbol,
                "quote": quote,
                "name": name,
                "price": price,           # price in USD (if quote is stable) or as per quote (if quote=SOL, price is in SOL units? DexScreener shows USD and native separately)
                "volume": volume,
                "change5m": change5m
            })
    return candidates

def get_token_info(symbol: str):
    """Use DexScreener search API to get token address and pair info for the given symbol on Solana.
    Returns a tuple (token_address, best_pair) where best_pair is a dict of pair info (including dexId, price, quote, liquidity)."""
    url = "https://api.dexscreener.com/latest/dex/search"
    params = {"q": symbol}
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
    except Exception as e:
        print(f"[!] DexScreener API search error for {symbol}: {e}")
        return None
    if "pairs" not in data:
        return None
    # Filter results for Solana chain and matching base token symbol
    sol_pairs = [p for p in data["pairs"] if p.get("chainId") == "solana" and p.get("baseToken", {}).get("symbol") == symbol]
    if not sol_pairs:
        return None
    # Select the pair with highest liquidity USD (or volume USD if provided)
    best_pair = None
    best_liquidity = 0
    for pair in sol_pairs:
        liq = pair.get("liquidity", {}).get("usd", 0) or 0
        if liq >= best_liquidity:
            best_liquidity = liq
            best_pair = pair
    if not best_pair:
        return None
    token_address = best_pair["baseToken"]["address"]
    return token_address, best_pair

def perform_swap(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100) -> str:
    """
    Perform a swap via Jupiter aggregator. Returns the transaction signature if successful.
    - input_mint, output_mint: token mint addresses (base58 strings) for input and output.
    - amount: input amount in smallest unit (e.g. lamports for SOL, minor units for tokens).
    - slippage_bps: slippage in basis points (100 = 1%).
    """
    # Jupiter v4 swap API
    # Step 1: Get swap routes and transaction from Jupiter API
    swap_url = "https://quote-api.jup.ag/v4/swap"
    swap_request = {
        "route": None,  # Let Jupiter pick best route automatically based on input/output and slippage
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": slippage_bps,
        "userPublicKey": user_pubkey_str
    }
    try:
        response = requests.post(swap_url, json=swap_request, timeout=10)
        swap_data = response.json()
    except Exception as e:
        print(f"[!] Jupiter API swap request error: {e}")
        return None
    # Check for errors in response
    if not swap_data or "swapTransaction" not in swap_data:
        err_msg = swap_data.get("error") if isinstance(swap_data, dict) else None
        print(f"[!] Jupiter swap API returned error: {err_msg}")
        return None
    swap_tx_b64 = swap_data["swapTransaction"]
    # Decode the base64 transaction and sign it with our keypair
    try:
        raw_tx = VersionedTransaction.from_bytes(base58.b64decode(swap_tx_b64))  # Use solders VersionedTransaction
        # Sign the transaction message with our keypair
        message_bytes = raw_tx.message.serialize()  # get serialized message bytes
        signature = solana_keypair.sign(message_bytes)
        # Populate the transaction with signature
        signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
        # Send the signed transaction to Solana RPC
        tx_encoded = base58.b64encode(bytes(signed_tx)).decode('utf-8')
        send_result = solana_client.send_raw_transaction(tx_encoded, opts=TxOpts(skip_confirmation=False, skip_preflight=True))
    except Exception as e:
        print(f"[!] Error signing or sending transaction: {e}")
        return None
    # Check send_result for signature
    if isinstance(send_result, dict) and send_result.get("result"):
        tx_signature = send_result["result"]
    elif isinstance(send_result, dict) and send_result.get("value"):  # older client returns .value maybe
        tx_signature = send_result.get("value")
    else:
        print(f"[!] Transaction send failed: {send_result}")
        return None
    return tx_signature

def open_trade(token: dict):
    """
    Attempt to open a trade for the given token info (from fetch_trending_tokens).
    Performs test buy & sell, then main buy, and adds to open_trades on success.
    """
    symbol = token["symbol"]
    quote = token["quote"]
    print(f"[*] Potential trade candidate: {symbol} (5m change {token['change5m']}%, volume {int(token['volume'])})")
    # Get token address and pair info via DexScreener API
    info = get_token_info(symbol)
    if not info:
        print(f"[!] Could not get token info for {symbol}, skipping.")
        return
    token_address, pair_info = info
    # Determine the appropriate output mint for test buy:
    # If quote is "USDC" or other stable, we might swap SOL -> token via USDC route or direct aggregator will handle it.
    # We'll just let Jupiter find route by specifying input SOL -> output token.
    sol_mint = "So11111111111111111111111111111111111111112"  # SOL mint address
    input_mint = sol_mint
    output_mint = token_address
    # Determine how many lamports represent TEST_TRADE_USD (0.01 USD)
    # We need SOL price to convert USD to lamports. Use DexScreener or pair info if available.
    lamports_for_test = None
    try:
        # If the token's quote is USDC or stable, we can approximate SOL price from pair_info if quoteToken is USDC:
        sol_price_usd = None
        if pair_info["quoteToken"]["symbol"] in ["USDC", "USDT"]:
            # If quote is stable, priceNative in DexScreener data might correspond to USD price (since quote is USD stable).
            sol_price_usd = None  # We need SOL price from elsewhere in this case
        # Otherwise if quote is SOL, DexScreener might provide priceUsd for the token, which implies SOL price:
        # priceNative is token price in SOL, priceUsd is token price in USD, so SOL price = priceUsd/priceNative
        if pair_info["quoteToken"]["symbol"] == "SOL":
            price_usd = Decimal(pair_info.get("priceUsd", "0"))
            price_sol = Decimal(pair_info.get("priceNative", "0"))
            if price_usd > 0 and price_sol > 0:
                sol_price_usd = price_usd / price_sol
        # If we still don't have SOL price, fetch from a simple API (Coingecko)
        if sol_price_usd is None:
            cg = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", timeout=5)
            sol_price_usd = Decimal(str(cg.json().get("solana", {}).get("usd", 0)))
        # Calculate lamports (1 SOL = 1e9 lamports)
        lamports_for_test = int((TEST_TRADE_USD / sol_price_usd) * 1_000_000_000)
        if lamports_for_test < 1000:
            lamports_for_test = 1000  # minimum lamports to avoid rounding to 0
    except Exception as e:
        print(f"[!] Error calculating test trade amount for {symbol}: {e}")
        return
    # 1. Test buy small amount
    tx_sig = perform_swap(input_mint, output_mint, lamports_for_test, slippage_bps=200)  # use 2% slippage for test
    if tx_sig is None:
        print(f"[!] Test buy swap failed for {symbol}, skipping trade.")
        return
    # 2. Test sell the acquired token amount (approx 0.01 USD worth of token)
    # Determine how much token we got: we can query token balance diff or approximate from price.
    # Easiest: query token account balance for our wallet after test buy.
    try:
        balances = solana_client.get_token_accounts_by_owner(Pubkey.from_string(user_pubkey_str), {"mint": token_address})
        # balances['result']['value'] is list of accounts, take the one for token
        token_balance = 0
        for acc in balances.get('result', {}).get('value', []):
            if acc.get('account', {}).get('data'):
                balance_info = solana_client.get_token_account_balance(acc['pubkey'])
                token_balance = int(balance_info['result']['value']['amount'])
                break
    except Exception as e:
        print(f"[!] Error fetching token balance for {symbol} after test buy: {e}")
        token_balance = 0
    if token_balance == 0:
        print(f"[!] Test buy for {symbol} did not yield token (balance 0), skipping.")
        return
    # Perform test sell: swap token -> SOL for the small amount acquired
    tx_sig2 = perform_swap(output_mint, input_mint, token_balance, slippage_bps=500)  # use 5% slippage on sell test to ensure execution
    if tx_sig2 is None:
        print(f"[!] Test sell swap failed for {symbol}, skipping trade.")
        return
    # 3. If both test buy and sell succeeded, proceed with main trade
    # Determine 30% of available SOL balance
    try:
        sol_balance_lamports = solana_client.get_balance(Pubkey.from_string(user_pubkey_str))["result"]["value"]
    except Exception as e:
        print(f"[!] Error fetching SOL balance: {e}")
        return
    sol_balance = Decimal(sol_balance_lamports) / Decimal(1_000_000_000)
    if sol_balance <= 0:
        print("[!] No SOL balance, cannot execute trade.")
        return
    trade_sol = sol_balance * TRADE_PORTION
    # Convert to lamports
    trade_amount_lamports = int(trade_sol * Decimal(1_000_000_000))
    if trade_amount_lamports < 1_000_000:  # require at least ~0.001 SOL to trade
        print(f"[!] Available SOL {sol_balance} too low for trade amount, skipping.")
        return
    # Execute main buy (SOL -> token)
    main_tx = perform_swap(input_mint, output_mint, trade_amount_lamports, slippage_bps=300)  # 3% slippage for main trade
    if main_tx is None:
        print(f"[!] Main buy swap failed for {symbol}, trade aborted.")
        return
    # Calculate token amount received from main trade:
    # Query token balance again after main buy
    new_balance = 0
    try:
        balance_info = solana_client.get_token_accounts_by_owner(Pubkey.from_string(user_pubkey_str), {"mint": token_address})
        if balance_info['result']['value']:
            token_acc = balance_info['result']['value'][0]['pubkey']
            bal = solana_client.get_token_account_balance(token_acc)
            new_balance = int(bal['result']['value']['amount'])
    except Exception as e:
        print(f"[!] Could not fetch token amount after main buy for {symbol}: {e}")
    if new_balance == 0:
        print(f"[!] Warning: token balance is zero after main buy for {symbol}. Trade might have failed.")
        return
    # Calculate amount_token in human-readable (adjust for token decimals)
    token_decimals = pair_info.get("baseToken", {}).get("decimals", 0)
    amount_token = Decimal(new_balance) / (Decimal(10) ** token_decimals)
    # Entry price (in USD) for reference: if quote is SOL, convert SOL spent to USD, divide by token amount
    entry_price_usd = None
    try:
        sol_price = None
        if quote == "SOL" and "priceUsd" in pair_info and "priceNative" in pair_info:
            # We have SOL price from earlier calculation
            price_usd = Decimal(pair_info["priceUsd"])
            price_sol = Decimal(pair_info["priceNative"])
            if price_sol > 0:
                sol_price = price_usd / price_sol
        if sol_price is None:
            cg = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", timeout=5)
            sol_price = Decimal(str(cg.json().get("solana", {}).get("usd", 0)))
        entry_price_usd = (trade_sol * sol_price) / amount_token
    except Exception:
        entry_price_usd = None
    # Create trade object and add to open_trades
    trade_obj = Trade(symbol=symbol, token_address=token_address, quote_symbol=quote,
                      amount_token=amount_token, initial_sol=trade_sol, entry_price_usd=entry_price_usd or Decimal(0))
    with trade_lock:
        open_trades[symbol] = trade_obj
    # Notify via Telegram
    entry_msg = f"Opened trade on {symbol} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    entry_msg += f"\n- Amount: {amount_token:.4f} {symbol}"
    entry_msg += f"\n- Cost: {float(trade_sol):.4f} SOL"
    if entry_price_usd:
        entry_msg += f"\n- Entry Price: ${float(entry_price_usd):.6f}"
    updater.bot.send_message(chat_id=CHAT_ID, text=entry_msg)  # Replace CHAT_ID with your Telegram chat ID (or use context in handlers)
    print(f"[+] Trade opened for {symbol}: spent {trade_sol:.4f} SOL for {amount_token:.4f} {symbol}")

def monitor_trades():
    """Check open trades for stop-loss or take-profit conditions and close them if needed."""
    if not open_trades:
        return
    # Fetch current price for each open trade from DexScreener API (pairs endpoint)
    symbols_to_close = []
    for symbol, trade in list(open_trades.items()):
        try:
            # Get current price via DexScreener pairs API for the specific token pair we traded
            pair_addr = None
            # If trade.quote is SOL, find pair address for token/SOL, else if USDC, find token/USDC
            query = symbol + "/" + trade.quote
            search = requests.get("https://api.dexscreener.com/latest/dex/search", params={"q": query}, timeout=5)
            data = search.json()
            current_price = None
            if data.get("pairs"):
                for p in data["pairs"]:
                    if p["chainId"] == "solana" and p["baseToken"]["symbol"] == symbol and p["quoteToken"]["symbol"] == trade.quote:
                        # If the pair matches the one we traded (same quote)
                        # priceNative is price in quote tokens, priceUsd is price in USD
                        if trade.quote == "SOL":
                            # If quote is SOL, priceNative is in SOL
                            price_in_sol = Decimal(p.get("priceNative", "0"))
                            current_value_sol = price_in_sol * trade.amount_token
                        else:
                            # If quote is USDC, priceNative is in USD (since quote is USD stable)
                            price_in_usd = Decimal(p.get("priceNative", "0"))
                            # Convert USD value to SOL
                            cg = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", timeout=5)
                            sol_price = Decimal(str(cg.json().get("solana", {}).get("usd", 0)))
                            current_value_sol = (price_in_usd * trade.amount_token) / sol_price
                        # Compare current value in SOL to initial SOL
                        current_factor = current_value_sol / trade.initial_sol
                        # Update trailing stop and check if should close
                        should_close = trade.update_stoploss(current_factor)
                        if should_close:
                            symbols_to_close.append(symbol)
                        break
        except Exception as e:
            print(f"[!] Error monitoring {symbol}: {e}")
    # Close any trades that hit stop-loss or take-profit
    for symbol in symbols_to_close:
        close_trade(symbol, triggered=True)

def close_trade(symbol: str, triggered: bool = False):
    """Close an open trade (sell token back to SOL). If triggered=True, it was closed by stoploss/TP; otherwise manual."""
    with trade_lock:
        trade = open_trades.get(symbol)
        if not trade:
            return "No open trade for token " + symbol
        # Remove trade from open_trades first to prevent re-entry issues
        open_trades.pop(symbol, None)
    # Perform swap of all token amount back to SOL
    token_address = trade.token_address
    amount_tokens_minor = int(trade.amount_token * (Decimal(10) ** int(pair_info.get("baseToken", {}).get("decimals", 0)))) if trade.amount_token else 0
    if amount_tokens_minor <= 0:
        return f"Trade {symbol}: nothing to sell."
    sol_mint = "So11111111111111111111111111111111111111112"
    tx_sig = perform_swap(token_address, sol_mint, amount_tokens_minor, slippage_bps=200)
    if tx_sig is None:
        msg = f"Trade {symbol}: attempted to close but swap failed!"
        updater.bot.send_message(chat_id=CHAT_ID, text=msg)
        print("[!] Close trade swap failed for", symbol)
        return msg
    # Calculate PNL
    # Fetch how much SOL we received from this swap by checking SOL balance difference or using Jupiter quote
    # Simpler: query SOL balance after (which might include others too). Instead, use the initial and current values from trailing logic.
    final_sol_value = None
    try:
        balance_after = solana_client.get_balance(Pubkey.from_string(user_pubkey_str))["result"]["value"]
        final_sol_value = Decimal(balance_after) / Decimal(1_000_000_000)
    except:
        final_sol_value = None
    pnl_msg = ""
    if final_sol_value is not None:
        profit_sol = final_sol_value - trade.initial_sol - (sol_balance if 'sol_balance' in locals() else 0)
        pnl_pct = (float(profit_sol) / float(trade.initial_sol) * 100) if trade.initial_sol > 0 and profit_sol != 0 else 0.0
        pnl_msg = f" PNL: {profit_sol:.4f} SOL ({pnl_pct:+.2f}%)."
    # Send Telegram notification
    close_reason = "stop-loss/TP" if triggered else "manual"
    message = f"Closed trade on {symbol} ({close_reason}).{pnl_msg}"
    updater.bot.send_message(chat_id=CHAT_ID, text=message)
    print(f"[-] Trade closed for {symbol}. {pnl_msg}")
    return message

# ============================== Scheduler and Telegram Handlers ==============================

def scan_market_job():
    """APScheduler job to scan market and monitor trades periodically."""
    try:
        # Monitor existing trades for stoploss/TP
        if open_trades:
            monitor_trades()
        # If we have capacity for new trades, scan DexScreener for opportunities
        if len(open_trades) < 3:
            candidates = fetch_trending_tokens()
            if not candidates:
                return
            # Avoid duplicate symbols and already open trades
            processed = set()
            for token in sorted(candidates, key=lambda x: x["change5m"], reverse=True):
                sym = token["symbol"]
                if sym in processed or sym in open_trades:
                    continue
                processed.add(sym)
                if len(open_trades) >= 3:
                    break  # reached max concurrent trades
                # Open trade for this token
                open_trade(token)
    except Exception as e:
        print(f"[!] Exception in scan_market_job: {e}")

# Telegram command handlers
def start_command(update, context):
    context.bot.send_message(chat_id=update.effective_chat.id,
                              text="🤖 Bot is running. Type /pnl to view open trades or /close <symbol> to close a trade.")

def pnl_command(update, context):
    with trade_lock:
        if not open_trades:
            context.bot.send_message(chat_id=update.effective_chat.id, text="No open trades at the moment.")
            return
        msg_lines = ["Open trades:"]
        for sym, trade in open_trades.items():
            # Approximate current PNL using last known peak or current value check
            # We can reuse monitor_trades logic to get current factor, but to keep it simple we'll use peak for now
            current_factor = trade.peak_value_factor
            if trade.trailing_active:
                # If trailing active, current value ~ peak (close to it); if not active, current ~ initial or below
                current_factor = trade.peak_value_factor
            pnl_pct = (float(current_factor) - 1) * 100
            msg_lines.append(f"{sym}: +{pnl_pct:.2f}% (unrealized)")
        context.bot.send_message(chat_id=update.effective_chat.id, text="\n".join(msg_lines))

def close_command(update, context):
    # /close <symbol>
    if len(context.args) == 0:
        context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /close <TOKEN_SYMBOL>")
        return
    symbol = context.args[0].upper()
    with trade_lock:
        if symbol not in open_trades:
            context.bot.send_message(chat_id=update.effective_chat.id, text=f"No open trade for {symbol}")
            return
    result_msg = close_trade(symbol, triggered=False)
    context.bot.send_message(chat_id=update.effective_chat.id, text=result_msg)

# Register command handlers
dispatcher.add_handler(CommandHandler("start", start_command))
dispatcher.add_handler(CommandHandler("pnl", pnl_command))
dispatcher.add_handler(CommandHandler("close", close_command, pass_args=True, filters=Filters.regex(r'^\S+$')))

# Start APScheduler job to scan every 10 seconds
scheduler = BackgroundScheduler(timezone=utc)
scheduler.add_job(scan_market_job, 'interval', seconds=10, max_instances=1, coalesce=True)
scheduler.start()

# Start the Telegram bot
updater.start_polling()
print("[*] Telegram bot started. Monitoring Solana markets...")
updater.idle()

# Shut down scheduler on exit
scheduler.shutdown(wait=False)
