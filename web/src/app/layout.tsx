import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KKB Lakehouse Agent · Yerel geliştirme",
  description: "Yerel uygulama ve veri deposu durumu",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="tr"><body>{children}</body></html>;
}
