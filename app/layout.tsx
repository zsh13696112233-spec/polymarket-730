import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

const title = "仓位观察｜Polymarket 钱包持仓监控";
const description =
  "清晰查看 Polymarket 钱包持仓，标出你与跟踪钱包的共同仓位、份额比例和仓位变动明细。";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const forwardedHost = requestHeaders
    .get("x-forwarded-host")
    ?.split(",")[0]
    ?.trim();
  const host = forwardedHost || requestHeaders.get("host") || "localhost:3000";
  const forwardedProtocol = requestHeaders
    .get("x-forwarded-proto")
    ?.split(",")[0]
    ?.trim();
  const localHost =
    host.startsWith("localhost") ||
    host.startsWith("127.0.0.1") ||
    host.startsWith("[::1]");
  const protocol = forwardedProtocol || (localHost ? "http" : "https");

  let metadataBase: URL;
  try {
    metadataBase = new URL(`${protocol}://${host}`);
  } catch {
    metadataBase = new URL("http://localhost:3000");
  }
  const previewImage = new URL("/og.png", metadataBase).toString();

  return {
    metadataBase,
    title,
    description,
    openGraph: {
      type: "website",
      title,
      description,
      images: [
        {
          url: previewImage,
          width: 1672,
          height: 941,
          alt: "仓位观察——看清持仓，安静跟随。",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [previewImage],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
