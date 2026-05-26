import { logger } from "@/lib/logger"
import { NextResponse } from "next/server";
import { apiError } from "@/lib/api-error";
import { checkRateLimit } from "@/lib/database";
import { verifyAuth } from "@/lib/auth";
import { z } from "zod";

const ChatAlertSchema = z.object({
  ticker: z.string().min(1).max(10).regex(/^[A-Z]{1,5}$/i),
  type: z.string().min(1).max(30),
  short_strike: z.number().finite().optional(),
  long_strike: z.number().finite().optional(),
  expiration: z.string().max(20).optional(),
  credit: z.number().finite().nonnegative().optional(),
  pop: z.number().finite().min(0).max(100).optional(),
  score: z.number().finite().min(0).max(100).optional(),
});

type ChatAlert = z.infer<typeof ChatAlertSchema>;

const RATE_LIMIT_MAX = 10;
const RATE_LIMIT_WINDOW_MS = 60_000;

const SYSTEM_PROMPT = `You are the Attix Trading Assistant — an expert in credit spread options strategies. You help users understand their trades, analyze market conditions, and learn options trading concepts.

Your personality:
- Concise and direct — traders don't want essays
- Use numbers and specifics, not vague advice
- Bullish/bearish bias based on real analysis, not cheerleading
- Admit uncertainty — say "I'd need to check" rather than guessing

You know about:
- Credit spreads (bull put, bear call), iron condors, debit spreads
- Greeks (delta, theta, gamma, vega) and how they affect positions
- Technical analysis (RSI, moving averages, support/resistance)
- Risk management (position sizing, max loss, profit targets)
- Market conditions (IV rank, VIX, sector rotation)
- The Attix alerts system (scans SPY/QQQ/IWM every 30 min, targets 70%+ PoP, 0.15-0.30 delta)

When users ask about a specific alert or trade, reference the actual numbers they provide. Keep responses under 150 words unless they ask for a deep explanation.

Format tips: Use bullet points for lists. Bold key numbers. Keep it scannable.`;

export async function POST(request: Request) {
  const authErr = await verifyAuth(request); if (authErr) return authErr;
  try {
    const forwarded = request.headers.get('x-forwarded-for');
    // Use the first (leftmost) IP — the client IP added by the outermost proxy
    const ip = forwarded
      ? forwarded.split(',')[0]?.trim() || 'unknown'
      : 'unknown';
    if (!checkRateLimit(`chat:${ip}`, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_MS)) {
      return apiError("Rate limit exceeded. Max 10 requests per minute.", 429);
    }

    const { messages, alerts } = await request.json();

    if (!messages || !Array.isArray(messages) || messages.length === 0) {
      return apiError("Messages required", 400);
    }

    // SEC-INJ-01: Sanitize user messages to prevent prompt injection
    const MAX_MESSAGE_LENGTH = 2000;
    const sanitizedMessages = messages
      .map((msg: { role?: string; content?: string }) => ({
        role: 'user' as const,  // Force all messages to 'user' role — never allow system/assistant injection
        content: typeof msg.content === 'string'
          ? msg.content.trim().slice(0, MAX_MESSAGE_LENGTH)
          : '',
      }))
      .filter((msg: { role: string; content: string }) => msg.content.length > 0);

    if (sanitizedMessages.length === 0) {
      return apiError("Messages required", 400);
    }

    // Build context with current alerts if available
    // SEC-INJ-02: Validate alerts with Zod schema before injecting into system prompt
    let contextPrompt = SYSTEM_PROMPT;
    if (alerts && Array.isArray(alerts) && alerts.length > 0) {
      const validAlerts: ChatAlert[] = [];
      for (const raw of alerts.slice(0, 5)) {
        const parsed = ChatAlertSchema.safeParse(raw);
        if (parsed.success) validAlerts.push(parsed.data);
      }
      if (validAlerts.length > 0) {
        const alertSummary = validAlerts.map((a) =>
          `${a.ticker} ${a.type}: ${a.short_strike ?? '?'}/${a.long_strike ?? '?'} exp ${a.expiration ?? '?'}, credit $${a.credit?.toFixed(2) ?? '?'}, PoP ${a.pop?.toFixed(0) ?? '?'}%, score ${a.score ?? '?'}`
        ).join('\n');
        contextPrompt += `\n\nCurrent active alerts:\n${alertSummary}`;
      }
    }

    // Try OpenAI first, then fallback to local responses
    const apiKey = process.env.OPENAI_API_KEY;
    
    if (apiKey) {
      const RETRYABLE = [429, 500, 503];
      const MAX_ATTEMPTS = 2;

      for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
        if (attempt > 0) await new Promise(r => setTimeout(r, 1000));

        const response = await fetch('https://api.openai.com/v1/chat/completions', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${apiKey}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            model: 'gpt-4o-mini',
            messages: [
              { role: 'system', content: contextPrompt },
              ...sanitizedMessages.slice(-10),
            ],
            max_tokens: 500,
            temperature: 0.7,
          }),
          signal: AbortSignal.timeout(15000),
        });

        if (response.ok) {
          const data = await response.json();
          const reply = data.choices?.[0]?.message?.content || "I couldn't generate a response.";
          return NextResponse.json({ reply });
        }

        const errorBody = await response.text().catch(() => 'unreadable');
        logger.error(`OpenAI API error ${response.status} (attempt ${attempt + 1})`, { error: String(errorBody) });

        if (!RETRYABLE.includes(response.status)) break;
      }
    }

    // Fallback: smart local responses based on keywords
    const lastMessage = sanitizedMessages[sanitizedMessages.length - 1]?.content?.toLowerCase() || '';
    const reply = generateLocalResponse(lastMessage, alerts);
    return NextResponse.json({ reply, fallback: true });

  } catch (error) {
    logger.error("Chat error", { error: String(error) });
    return apiError("Chat failed", 500);
  }
}

function generateLocalResponse(message: string, alerts?: ChatAlert[]): string {
  // Credit spread questions
  if (message.includes('credit spread') || message.includes('what is a')) {
    return `**Credit spreads** are options strategies where you sell a higher-premium option and buy a lower-premium option at a different strike, collecting a net credit.

• **Bull Put Spread** — bullish, sell a put + buy a lower put
• **Bear Call Spread** — bearish, sell a call + buy a higher call

Your max profit is the credit received. Max loss is the spread width minus the credit. Attix targets spreads with **70%+ probability of profit**.`;
  }

  if (message.includes('delta') || message.includes('greek')) {
    return `**Key Greeks for credit spreads:**

• **Delta** (0.15-0.30 target) — probability the short strike gets breached. Lower = safer but less premium
• **Theta** — time decay working in your favor. Credit spreads profit from theta
• **Vega** — IV sensitivity. High IV at entry = more premium collected
• **Gamma** — acceleration risk. Increases near expiry, which is why we close at 7 DTE

Attix targets **0.15-0.30 delta** on the short leg — the sweet spot between premium and safety.`;
  }

  if (message.includes('pop') || message.includes('probability') || message.includes('win rate')) {
    return `**Probability of Profit (PoP)** measures the likelihood a credit spread expires profitable.

Attix filters for **70%+ PoP** — meaning roughly 7 out of 10 trades should be winners. Combined with disciplined risk management (50% profit target, 2.5x stop loss), this creates a strong edge over time.

Higher PoP = less premium collected but more consistent wins. It's a tradeoff — we optimize for the sweet spot.`;
  }

  if (message.includes('risk') || message.includes('position size') || message.includes('how much')) {
    return `**Attix Risk Management:**

• **Max 2% risk per trade** — on a $100K account, max loss per trade is $2,000
• **Max 5 concurrent positions** — limits total portfolio risk to ~10%
• **50% profit target** — close winners early, don't get greedy
• **2.5x stop loss** — cut losers before max loss
• **Close at 7 DTE** — avoid gamma risk near expiration

This means even a losing streak won't blow up the account. Consistency over home runs.`;
  }

  if (message.includes('spy') || message.includes('qqq') || message.includes('iwm') || message.includes('market')) {
    const alertCount = alerts?.length || 0;
    return `**Current Market Scan:**

Attix monitors **SPY, QQQ, and IWM** — the three most liquid ETFs for credit spreads. High volume = tight bid-ask spreads = better fills.

${alertCount > 0 ? `We currently have **${alertCount} active alerts**. The system scans every 30 minutes during market hours (9:45 AM - 3:45 PM ET).` : 'No active alerts right now. The system scans every 30 minutes during market hours (9:45 AM - 3:45 PM ET).'}

Each scan analyzes the full options chain, runs technical analysis, and scores opportunities by probability of profit.`;
  }

  if (message.includes('paper trad') || message.includes('how do i') || message.includes('get started')) {
    return `**Getting started with paper trading:**

1. Browse the **Today's Alerts** page for current opportunities
2. Click **"Paper Trade"** on any alert you like
3. Go to **My Trades** to track your positions and P&L
4. Trades auto-close at profit target, stop loss, or expiration

You start with a **$100K virtual balance**. No real money at risk — just practice with real market data. Try picking the highest PoP alerts first to build confidence.`;
  }

  if (message.includes('hello') || message.includes('hi') || message.includes('hey')) {
    return `Hey! 👋 I'm the Attix Trading Assistant. I can help you with:

• **Understanding alerts** — what the numbers mean
• **Credit spread basics** — Greeks, risk, strategy
• **Your trades** — analysis and suggestions
• **Market context** — what's moving and why

What would you like to know?`;
  }

  // Default
  return `Great question! I can help with:

• **Credit spreads** — strategy, Greeks, risk management
• **Current alerts** — what our scanner found
• **Paper trading** — how to track your picks
• **Market analysis** — SPY/QQQ/IWM outlook

Try asking something like "What does 85% PoP mean?" or "How do I pick the best alert?"`;
}
