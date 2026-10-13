import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vantara | Operações",
  description: "Exceções de recebíveis e contas a pagar com evidências para revisão financeira",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR"><body>{children}</body></html>;
}
