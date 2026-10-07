import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vantara | Operações",
  description: "Investigue pendências nos exports de Protheus, Omie, banco e contas a pagar",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR"><body>{children}</body></html>;
}
