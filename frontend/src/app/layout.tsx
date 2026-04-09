import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MM Sanford Internal Reporting",
  description: "Automated ETL and AI-driven insights for digital marketing performance.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className="antialiased"
      >
        {children}
      </body>
    </html>
  );
}
