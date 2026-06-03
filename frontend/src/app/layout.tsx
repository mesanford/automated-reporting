import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { WorkspaceProvider } from "@/lib/workspace";
import { AuthGate } from "@/components/AuthGate";

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
      <body className="antialiased">
        <AuthProvider>
          <AuthGate>
            <WorkspaceProvider>{children}</WorkspaceProvider>
          </AuthGate>
        </AuthProvider>
      </body>
    </html>
  );
}
