import type { Metadata } from "next";
import { SidebarNav } from "./SidebarNav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Daily Info",
  description: "Self-hosted research reading desk",
  icons: [{ rel: "icon", url: "/favicon.svg" }],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="shell">
          <aside className="sidebar">
            <div className="brand">
              <span className="brandMark">DI</span>
              <div>
                <strong>Daily Info</strong>
                <span>Research desk</span>
              </div>
            </div>
            <SidebarNav />
          </aside>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
