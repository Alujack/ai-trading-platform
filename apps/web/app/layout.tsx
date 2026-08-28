import type { Metadata } from "next";
import { Hanken_Grotesk, JetBrains_Mono } from "next/font/google";
import { Shell } from "./components/dash/Shell";
import "./globals.css";

const hanken = Hanken_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-hanken",
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: {
    default: "Cambotix Trade Intelligence",
    template: "%s · Cambotix Trade Intelligence",
  },
  description: "AI-assisted market context, disciplined risk controls, and execution clarity in one focused trading workspace.",
  openGraph: {
    type: "website",
    title: "Cambotix Trade Intelligence",
    description: "AI-assisted market context, risk controls, and execution clarity.",
    images: [{ url: "/og.png", width: 1731, height: 909, alt: "Cambotix Trade Intelligence" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Cambotix Trade Intelligence",
    description: "AI-assisted market context, risk controls, and execution clarity.",
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${hanken.variable} ${jetbrains.variable}`}>
      <body className="antialiased" style={{ background: "#080a0d", color: "#f1f3f5", fontFamily: "var(--font-hanken), system-ui, sans-serif" }}>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
