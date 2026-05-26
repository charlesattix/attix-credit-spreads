'use client'

import { useState, useRef, useEffect } from 'react'
import { Sparkles, Send, Loader2, MessageSquare, ChevronDown } from 'lucide-react'
import { apiFetch } from '@/lib/api'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
}

const QUICK_PROMPTS = [
  "What is a credit spread?",
  "Explain the Greeks",
  "How do I pick alerts?",
  "What does PoP mean?",
]

export function AIChat({ forceExpanded }: { forceExpanded?: boolean } = {}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const scrollToBottom = () => {
    const container = messagesEndRef.current?.parentElement
    if (container) {
      container.scrollTop = container.scrollHeight
    }
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  useEffect(() => {
    if (expanded && inputRef.current) {
      inputRef.current.focus()
    }
  }, [expanded])

  const sendMessage = async (text?: string) => {
    const messageText = text || input.trim()
    if (!messageText || loading) return

    const userMessage: Message = { id: crypto.randomUUID(), role: 'user', content: messageText, timestamp: new Date() }
    const newMessages = [...messages, userMessage]
    setMessages(newMessages)
    setInput('')
    setLoading(true)

    try {
      const data = await apiFetch<{ reply?: string }>('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: newMessages.map(m => ({ role: m.role, content: m.content })),
        }),
      })
      
      const assistantMessage: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: data.reply || "Sorry, I couldn't process that. Try again!",
        timestamp: new Date(),
      }
      setMessages([...newMessages, assistantMessage])
    } catch {
      setMessages([...newMessages, {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: "Connection error. Please try again.",
        timestamp: new Date(),
      }])
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  // Collapsed state — compact card
  if (!expanded) {
    return (
      <div 
        className="bg-white rounded-lg border border-border p-4 cursor-pointer hover:border-brand-purple/30 hover:shadow-md transition-all"
        onClick={() => setExpanded(true)}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-gradient-brand rounded-full flex items-center justify-center">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div>
              <h3 className="font-semibold text-sm">AI Assistant</h3>
              <p className="text-xs text-muted-foreground">Ask about trades & strategy</p>
            </div>
          </div>
          <MessageSquare className="w-5 h-5 text-brand-purple" />
        </div>

        {/* Quick prompt chips */}
        <div className="flex flex-wrap gap-1.5 mt-3">
          {QUICK_PROMPTS.slice(0, 2).map((prompt) => (
            <button
              key={prompt}
              onClick={(e) => {
                e.stopPropagation()
                setExpanded(true)
                setTimeout(() => sendMessage(prompt), 100)
              }}
              className="px-2.5 py-1 text-xs font-medium text-brand-purple bg-brand-purple/5 border border-brand-purple/20 rounded-full hover:bg-brand-purple/10 transition-colors"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>
    )
  }

  // Expanded state — full chat
  return (
    <div className={`bg-white overflow-hidden flex flex-col ${forceExpanded ? 'h-full' : 'rounded-lg border border-border'}`} style={forceExpanded ? undefined : { height: '480px' }}>
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border bg-gradient-to-r from-brand-purple/5 to-brand-pink/5">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 bg-gradient-brand rounded-full flex items-center justify-center">
            <Sparkles className="w-3.5 h-3.5 text-white" />
          </div>
          <div>
            <h3 className="font-semibold text-sm">Attix Assistant</h3>
            <div className="flex items-center gap-1">
              <div className="w-1.5 h-1.5 bg-profit rounded-full animate-pulse"></div>
              <span className="text-[10px] text-muted-foreground">Online</span>
            </div>
          </div>
        </div>
        {!forceExpanded && (
          <button 
            onClick={() => setExpanded(false)}
            className="p-1 hover:bg-secondary rounded transition-colors"
          >
            <ChevronDown className="w-4 h-4 text-muted-foreground" />
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-2">
            <div className="w-12 h-12 bg-gradient-brand rounded-full flex items-center justify-center mb-3 opacity-80">
              <Sparkles className="w-6 h-6 text-white" />
            </div>
            <p className="text-sm font-medium mb-1">How can I help?</p>
            <p className="text-xs text-muted-foreground mb-4">Ask about credit spreads, Greeks, alerts, or trading strategy.</p>
            
            {/* Quick prompts */}
            <div className="flex flex-wrap gap-1.5 justify-center">
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  onClick={() => sendMessage(prompt)}
                  className="px-2.5 py-1.5 text-xs font-medium text-brand-purple bg-brand-purple/5 border border-brand-purple/20 rounded-full hover:bg-brand-purple/10 transition-colors"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 ${
                msg.role === 'user'
                  ? 'bg-brand-purple text-white rounded-br-md'
                  : 'bg-secondary/70 text-foreground rounded-bl-md'
              }`}>
                <div className={`text-[13px] leading-relaxed whitespace-pre-wrap ${
                  msg.role === 'assistant' ? 'prose-sm' : ''
                }`}>
                  {msg.role === 'assistant' ? <FormatText text={msg.content} /> : msg.content}
                </div>
                <div className={`text-[10px] mt-1 ${
                  msg.role === 'user' ? 'text-white/60' : 'text-muted-foreground/60'
                }`}>
                  {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </div>
              </div>
            </div>
          ))
        )}
        
        {loading && (
          <div className="flex justify-start">
            <div className="bg-secondary/70 rounded-2xl rounded-bl-md px-4 py-3">
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 bg-brand-purple/40 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                <div className="w-2 h-2 bg-brand-purple/40 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                <div className="w-2 h-2 bg-brand-purple/40 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
              </div>
            </div>
          </div>
        )}
        
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-2.5 border-t border-border bg-white">
        <div className="flex items-center gap-2 bg-secondary/50 rounded-lg px-3 py-1.5">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about trades, strategy..."
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/50"
            disabled={loading}
          />
          <button
            onClick={() => sendMessage()}
            disabled={!input.trim() || loading}
            className={`p-1.5 rounded-lg transition-all ${
              input.trim() && !loading
                ? 'bg-gradient-brand text-white shadow-sm hover:shadow-md'
                : 'text-muted-foreground/30'
            }`}
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

// Render markdown-style bold text as JSX
function FormatText({ text }: { text: string }) {
  const parts = text.split(/(\*\*.*?\*\*)/g)
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={i} className="font-semibold">{part.slice(2, -2)}</strong>
        }
        return <span key={i}>{part}</span>
      })}
    </>
  )
}
