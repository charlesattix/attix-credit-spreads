# Alerts by Attix - Credit Spread Trading Dashboard

AI-powered credit spread trading alerts with high probability of profit.

## Design Language

**Light, modern, consumer-friendly UI:**
- Light theme: Background #FAFAFA, white cards with subtle borders
- Brand gradient: Purple (#9B6DFF) → Pink (#E84FAD) → Orange (#F59E42)
- Font: Inter with -apple-system fallback
- Rounded cards (12-16px radius), subtle shadows
- Green (#10B981) for profits/wins, Red (#EF4444) for losses

## Features

- 📊 **Live Alerts Feed** - Real-time credit spread opportunities with AI-powered analysis
- 📈 **Scrolling Ticker Tape** - Live market prices for SPY, QQQ, NVDA, TSLA, and more
- 💬 **AI Chat Assistant** - Ask questions about trades and get instant answers
- 📅 **Performance Heatmap** - 28-day visual performance tracking
- 🎯 **Smart Filtering** - Filter by Bullish, Bearish, Neutral, or High Probability
- 📊 **Detailed Analytics** - Win rate, P&L, profit factor, and more
- 🔄 **Auto-Refresh** - Dashboard updates every 60 seconds
- 📱 **Responsive Design** - Works perfectly on desktop, tablet, and mobile

## Tech Stack

- **Framework**: Next.js 14+ (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS (light theme)
- **Charts**: Recharts
- **Icons**: Lucide React
- **Notifications**: Sonner

## Setup

### Prerequisites

- Node.js 18+
- Python backend running at `../` (relative to this directory)

### Installation

```bash
npm install
```

### Development

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000)

### Production Build

```bash
npm run build
npm start
```

## Layout Structure

### Navbar
- Logo (diamond-shaped with gradient)
- Nav links: Today's Alerts, History, Leaderboard, Learn
- Live markets indicator (green pulse dot)
- "Get Premium" CTA button

### Ticker Tape
- Scrolling animation (30s infinite)
- Shows SPY, QQQ, NVDA, TSLA, AAPL, MSFT, AMZN, META
- Price + % change with up/down arrows

### Stats Bar
- 5 columns: Today's Alerts, Avg Prob of Profit, 30-Day Win Rate, Avg Return/Trade, Alerts This Week

### Main Content
- **Left**: Alerts feed with expandable cards
- **Right**: 340px sidebar with AI chat, performance stats, heatmap, upsell

### Alert Cards
- White background with subtle border
- Left purple accent border for new alerts
- Type badge (Bullish/Bearish/Neutral) with colored background
- "AI-Powered" pill badge
- Large ticker symbol + price
- 3-column stats: Max Profit (green), Max Loss (red), Prob of Profit (progress bar)
- Expandable for trade legs, details, and "Why This Trade?" reasoning
- Hover effect: shadow elevation + purple border hint

### Sidebar Components
1. **AI Chat**: Gradient avatar, chat bubbles, suggestion chips, text input
2. **30-Day Performance**: Total alerts, winners, losers, win rate, avg winner/loser, profit factor
3. **Recent 28 Days Heatmap**: 7×4 grid of colored squares (green=win, red=loss, gray=none)
4. **Upsell Card**: Gradient background with "Unlock Premium" CTA

## Data Integration

### API Routes
- `/api/alerts` - Fetch alerts from `output/alerts.json`
- `/api/positions` - Get open positions from `data/trades.json`
- `/api/trades` - Get all trades
- `/api/config` - Read/write `config.yaml`
- `/api/scan` - Trigger `python3 main.py scan`
- `/api/backtest` - Fetch backtest results
- `/api/backtest/run` - Trigger `python3 main.py backtest`

### Python Backend Files
- `../output/alerts.json` - Latest alert opportunities
- `../data/trades.json` - Open and closed positions
- `../config.yaml` - System configuration

## Animations

- **Ticker scrolling**: 30s linear infinite
- **Pulse dot**: 2s ease-in-out infinite (live markets indicator)
- **Card hover**: Shadow elevation, purple border hint
- **Alert expand/collapse**: Smooth height transition

## Color Palette

```css
Background: #FAFAFA
Cards: #FFFFFF
Border: #E5E7EB
Text: #111827
Muted: #6B7280

Brand Purple: #9B6DFF
Brand Pink: #E84FAD
Brand Orange: #F59E42

Profit/Green: #10B981
Loss/Red: #EF4444
Neutral/Yellow: #F59E0B
```

## Development Tips

### Adding New Alert Types
1. Update the type badge logic in `alert-card.tsx`
2. Add appropriate color scheme
3. Update filter logic in main page

### Customizing Stats
1. Edit `stats-bar.tsx` for top-level metrics
2. Edit `performance-card.tsx` for sidebar stats

### Modifying AI Chat
1. Update suggestion chips in `ai-chat.tsx`
2. Add backend integration for actual AI responses

## Troubleshooting

### Build Errors
```bash
rm -rf .next node_modules
npm install
npm run build
```

### API Connection Issues
Ensure Python backend is running and output files exist:
- Check `../output/alerts.json`
- Check `../data/trades.json`

### Port Already in Use
```bash
npm run dev -- -p 3001
```

## License

Part of the Credit Spread Trading System by Attix.
