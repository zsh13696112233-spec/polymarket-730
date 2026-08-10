# Position Watch｜Polymarket Wallet Position Monitor

A local Polymarket wallet position monitor and limited auto copy-trading tool. Public watch focuses only on actual positions:

- Switch between multiple labeled wallets
- Set a global copy ratio to show suggested hold targets on current positions, and compute suggested buy/sell shares plus estimated USDC for later net opens, adds, reduces, or closes
- Set a read-only “My Wallet” to auto-highlight overlapping positions with watched wallets in the same market and direction
- Overlapping positions show both sides’ share ratios, and can expand to show each side’s cost, market value, P&L, and buy lots
- While still co-holding, the other wallet’s adds, reduces, or closes generate markable in-app alerts
- Positions sorted by current market value, high to low
- Show average price, current price, shares, cost, market value, and P&L
- Backfill public trades, and view remaining buy lots by Beijing time for `All` or a specific purchase date
- Same position bought across days keeps independent share, cost, market value, and P&L; reduces use FIFO
- Regular positions are checked every 15 seconds; after a change stays stable for 15 seconds (max wait 60 seconds), emit copy-consumable position events
- Record on-chain redemptions, showing redeemed shares, received USDC, and transaction hash
- Expand change history to inspect matching fills
- One-click jump to the Polymarket market page
- One execution wallet can configure and run live copy-trading for multiple watched wallets at once
- Auto copy applies to all markets still open for trading, and only handles opens, closes, and redemptions; adds and reduces are monitor-only

Public monitoring does not read the target wallet’s open orders. Each wallet must be explicitly confirmed on the page before live trading starts; turning the switch off stops new buys, but continues following closes and redemptions. A separate $1 one-sided FAK dry run spends real funds to verify wallet, fees, and balances. Live private keys never enter the database, API, or logs; they can only be imported into the macOS Keychain via a hidden CLI.

## Three-Event Auto Copy V2

After selecting a watched wallet on the page, configure four parameters independently for that wallet in the “Live Auto Copy” card: 10% copy ratio, $20 max per position, $160 fixed wallet budget, and ±5¢ current book protection. The sum of fixed budgets across running wallets cannot exceed the execution wallet’s real-time available capital; budget, cash reserve, daily buy cap, and loss circuit breaker are controlled across all wallets combined.

The dashboard shows both current attributed positions and historical cycles. Current positions estimate sellable market value, unrealized P&L, and total P&L from the CLOB best bid; history shows cumulative invested, cumulative sold, and realized P&L. Live order records include the V2 signed order hash, order ID, trade ID, and actual fees.

The copy engine only reads stable events from regular position monitoring: `opened` executes one proportional FAK buy based on the watched wallet’s open cost; `increased` and `decreased` are ignored; `closed` sells the full attributed position once; `redeemed` redeems the full attributed position once. Opening the same asset again after a close or redemption starts a new cycle. There is no kickoff cutoff; the system only confirms the market is currently open for trading.

The trading client uses `py-clob-client-v2==1.1.0`, pUSD, and V2 Exchange; it supports legacy Magic/Proxy signature type `1` and the newer Deposit Wallet type `3`, with new configs defaulting to type `3`. After binding “My Wallet”, enter the signing EOA and Polymarket funding wallet, then import the execution private key from the terminal:

```bash
uv run python -m backend.copy_cli set-key --account 0xYourSigningWalletAddress
```

Private key input is not echoed. For Proxy wallets that need auto-redemption, also join the Polymarket Builder Program and save Builder credentials to the Keychain:

```bash
uv run python -m backend.copy_cli set-builder-creds --account 0xYourSigningWalletAddress
```

Then return to the page to verify the signing address, Proxy address, pUSD balance, and V2 Exchange approvals. The $1 dry run requires a second confirmation, must stay at or under $1 including fees, and only shows success when fills, pUSD, and outcome token balances match; dry-run positions are not included in auto copy. Use a dedicated hot wallet and do not trade that wallet manually.

## Requirements

- Node.js `>=22.13.0`
- [uv](https://docs.astral.sh/uv/)

## First-time setup

```bash
npm ci
UV_CACHE_DIR=.uv-cache uv sync
```

To change default detection parameters, copy `.env.example` to `.env` and adjust as needed.

## Local run

Development mode:

```bash
npm run dev:all
```

The start script first cleanly stops any old services on ports `3000` or `8730`, then starts new processes.

Open [http://localhost:3000](http://localhost:3000), then add a wallet address or Polymarket profile link on the page.

Stable run:

```bash
npm run build
npm run start:local
```

Both commands listen on localhost only. Monitoring stops when the terminal is closed.

## Data

The SQLite database is stored by default at:

```text
data/polymarket-watch.db
```

Wallets, current positions, public trades, pending merged changes, and history details are all persisted by the backend. Disabling a wallet does not delete history.

## Verification

```bash
npm test
npm run lint
```

Backend API docs are available at [http://127.0.0.1:8730/docs](http://127.0.0.1:8730/docs) after the service starts.
