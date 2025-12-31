import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Job Scheduler Dashboard",
  description: "Distributed job queue management dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased bg-gray-50 dark:bg-gray-900 min-h-screen">
        {children}
      </body>
    </html>
  );
}
