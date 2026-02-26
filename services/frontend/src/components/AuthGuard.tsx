"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { hasValidToken, getAccessToken, isTokenExpired } from "@/lib/auth-storage";

const PUBLIC_PATHS = ["/login", "/register"];

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const isPublic = PUBLIC_PATHS.includes(pathname);

    if (isPublic) {
      // If already authenticated, redirect to dashboard
      if (hasValidToken()) {
        router.replace("/dashboard");
        return;
      }
      setChecked(true);
      return;
    }

    // Protected route — require valid token
    const token = getAccessToken();
    if (!token || isTokenExpired(token)) {
      router.replace("/login");
      return;
    }

    setChecked(true);
  }, [pathname, router]);

  if (!checked) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return <>{children}</>;
}
