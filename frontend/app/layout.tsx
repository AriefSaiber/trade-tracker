import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { BackendStatusBanner } from "@/components/BackendStatusBanner";

export const metadata: Metadata = {
  title: "AlgoTrader AI",
  description: "Local-first algorithmic trading platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 overflow-x-hidden">
            <BackendStatusBanner />
            <div className="px-6 py-6 lg:px-10">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
