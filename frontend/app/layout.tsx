import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

// next/font는 폰트를 빌드 시점에 내려받아 셀프 호스팅합니다(외부 요청/깜빡임 없음).
const geistSans = Geist({ subsets: ["latin"], variable: "--font-geist-sans" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata: Metadata = {
  title: "Meeting detail",
  description: "회의록 상세: 액션아이템 조회 및 관리",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className={`${geistSans.variable} ${geistMono.variable}`} suppressHydrationWarning>
      <body className="mn-body min-h-screen">{children}</body>
    </html>
  );
}
