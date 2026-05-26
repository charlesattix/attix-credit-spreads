import type { Metadata } from "next"
import { Inter } from "next/font/google"
import "./globals.css"
import { Navbar } from "@/components/layout/navbar"
import { Ticker } from "@/components/layout/ticker"
import { Toaster } from "sonner"

const inter = Inter({ subsets: ["latin"] })

export const metadata: Metadata = {
  title: "Alerts by Attix - Smart Options Trading Alerts",
  description: "AI-powered credit spread trading alerts with high probability of profit",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <Navbar />
        <Ticker />
        {children}
        <Toaster position="top-right" />
      </body>
    </html>
  )
}
