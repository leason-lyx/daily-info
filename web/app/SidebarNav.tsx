"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Library, Palette, Settings, SquareKanban } from "lucide-react";

const nav = [
  { href: "/", label: "Feed", icon: SquareKanban },
  { href: "/visual-directions", label: "Visuals", icon: Palette },
  { href: "/sources", label: "Sources", icon: Library },
  { href: "/health", label: "Health", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings },
];

function isActivePath(pathname: string, href: string) {
  if (href === "/") {
    return pathname === "/";
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SidebarNav() {
  const pathname = usePathname();

  return (
    <nav className="sidebarNav" aria-label="Primary navigation">
      {nav.map((item) => {
        const active = isActivePath(pathname, item.href);

        return (
          <Link key={item.href} href={item.href} className={`navItem${active ? " active" : ""}`} aria-current={active ? "page" : undefined}>
            <span className="navIcon" aria-hidden="true">
              <item.icon size={18} />
            </span>
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
