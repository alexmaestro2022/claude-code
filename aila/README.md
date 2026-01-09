# AILA - AI-powered Algorithmic Trading System

AILA is an automated trading system for cryptocurrency markets, implementing the Triple SuperTrend strategy with optional EMA200 filter. It supports both spot and futures trading on Bybit exchange.

## Features

- **Triple SuperTrend Strategy**: Uses three SuperTrend indicators with different parameters for high-confidence signals
- **EMA200 Filter**: Optional trend filter to trade only in the direction of the major trend
- **Flexible Risk Management**:
  - Stop-loss based on SuperTrend lines, fixed percentage, or ATR
  - Take-profit with risk-reward ratios or multiple targets
  - Trailing stop with configurable activation
- **Spot & Futures Trading**: Support for both account types with configurable leverage
- **Real-time Trading Engine**: Async trading engine with signal processing and position management
- **Backtesting**: Test strategies on historical data before going live
- **Notifications**: Telegram integration for trade alerts

## Installation

### Prerequisites

- Python 3.11 or higher
- pip or poetry for package management

### Setup

1. Clone the repository:
```bash
git clone https://github.com/your-repo/aila.git
cd aila
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate     # Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment:
```bash
cp .env.example .env
# Edit .env with your settings
```

## Configuration

### API Keys

Get your API keys from Bybit:
1. Log in to [Bybit](https://www.bybit.com)
2. Go to API Management
3. Create a new API key with trading permissions
4. Copy the API key and secret to your `.env` file

### Strategy Parameters

Edit `config/strategies.yaml` or use environment variables:

```yaml
# SuperTrend settings
supertrend:
  st1:
    period: 12
    multiplier: 3.0
  st2:
    period: 11
    multiplier: 2.0
  st3:
    period: 10
    multiplier: 1.0

# EMA filter
ema:
  enabled: true
  period: 200
  filter_mode: strict  # strict | soft
```

### Risk Management

```yaml
risk:
  stop_loss:
    mode: supertrend_line  # Use ST2 line as stop-loss
    supertrend_line: 2

  take_profit:
    mode: risk_ratio
    risk_ratio: 2.0  # 2:1 reward-to-risk

  position:
    risk_per_trade: 2.0  # 2% of balance per trade
    max_open_positions: 3
```

## Usage

### Running the Bot

```bash
# Start the trading bot
python -m aila.scripts.run_bot

# Or use the entry point
aila
```

### Running Backtests

```bash
# Run backtest with default settings
python -m aila.scripts.run_backtest --symbol BTCUSDT --days 90

# Custom backtest
python -m aila.scripts.run_backtest \
    --symbol ETHUSDT \
    --timeframe 4h \
    --start 2024-01-01 \
    --end 2024-06-30 \
    --initial-balance 10000 \
    --leverage 5 \
    --output results.csv
```

### API Usage

```python
from aila.core.strategy import TripleSuperTrendStrategy, TripleSuperTrendConfig
from aila.exchange import BybitClient, BybitConfig

# Configure client
config = BybitConfig(
    api_key="your_key",
    api_secret="your_secret",
    testnet=True,
)

client = BybitClient(config)
client.connect()

# Create strategy
strategy = TripleSuperTrendStrategy()

# Get market data
df = client.get_klines("BTCUSDT", interval="60", limit=250)

# Generate signal
signal = strategy.process(df, "BTCUSDT")
print(f"Signal: {signal}")
```

## Project Structure

```
aila/
├── config/                 # Configuration files
│   ├── settings.py         # Main settings
│   ├── strategies.yaml     # Strategy configurations
│   └── logging.yaml        # Logging configuration
│
├── core/                   # Core trading logic
│   ├── indicators/         # Technical indicators
│   │   ├── supertrend.py   # SuperTrend indicator
│   │   ├── ema.py          # EMA indicator
│   │   └── atr.py          # ATR indicator
│   ├── strategy/           # Trading strategies
│   │   ├── base.py         # Base strategy class
│   │   └── triple_supertrend.py
│   └── risk/               # Risk management
│       ├── position_sizing.py
│       ├── stop_loss.py
│       └── take_profit.py
│
├── exchange/               # Exchange integration
│   ├── bybit_client.py     # Bybit API client
│   ├── spot.py             # Spot trading
│   ├── futures.py          # Futures trading
│   └── models.py           # Data models
│
├── trading/                # Trading engine
│   ├── engine.py           # Main trading engine
│   ├── order_manager.py    # Order management
│   ├── position_manager.py # Position management
│   └── executor.py         # Trade execution
│
├── scripts/                # Entry points
│   ├── run_bot.py          # Start trading bot
│   └── run_backtest.py     # Run backtests
│
└── utils/                  # Utilities
    ├── exceptions.py       # Custom exceptions
    └── helpers.py          # Helper functions
```

## Strategy Logic

### Entry Signals

**LONG Signal:**
- All three SuperTrend indicators are green (direction = 1)
- Price is above EMA200 (if EMA filter is enabled in strict mode)

**SHORT Signal:**
- All three SuperTrend indicators are red (direction = -1)
- Price is below EMA200 (if EMA filter is enabled in strict mode)

### Exit Signals

Positions are closed when:
1. Stop-loss is hit (SuperTrend line, fixed %, or ATR-based)
2. Take-profit is reached (based on R:R ratio)
3. Signal reverses (all SuperTrends flip to opposite direction)

## Safety Features

- **Maximum Daily Loss**: Stop trading after reaching daily loss limit
- **Maximum Drawdown**: Emergency stop at drawdown threshold
- **Consecutive Loss Limit**: Cooldown period after losing streak
- **Position Limits**: Maximum number of concurrent positions
- **Minimum Balance**: Prevent trading below minimum balance

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Formatting

```bash
black aila/
ruff check aila/
```

### Type Checking

```bash
mypy aila/
```

## Docker Deployment

```bash
docker-compose up -d
```

## Disclaimer

This software is for educational purposes only. Trading cryptocurrency involves substantial risk of loss. Never trade with money you cannot afford to lose. The developers are not responsible for any financial losses incurred from using this software.

## License

MIT License - see LICENSE file for details.

## Support

- Create an issue on GitHub for bug reports
- Check the documentation for common questions
- Join our community for discussions
