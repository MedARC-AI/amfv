import { Link } from "@tanstack/react-router"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const content =
    variant === "responsive" ? (
      <>
        <span
          className={cn(
            "flex items-center gap-2 text-primary-foreground group-data-[collapsible=icon]:hidden",
            className,
          )}
        >
          <img
            alt="MedARC"
            className="h-6 w-auto"
            src="/medarc-logo-horizontal-white.webp"
          />
          <span className="font-semibold tracking-normal">AMFV Web</span>
        </span>
        <span
          className={cn(
            "hidden size-7 items-center justify-center text-primary-foreground text-sm font-semibold group-data-[collapsible=icon]:inline-flex",
            className,
          )}
        >
          A
        </span>
      </>
    ) : (
      <span
        className={cn(
          variant === "full"
            ? "font-semibold tracking-normal"
            : "inline-flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground text-xs font-semibold",
          className,
        )}
      >
        {variant === "full" ? "AMFV Web" : "A"}
      </span>
    )

  if (!asLink) {
    return content
  }

  return <Link to="/">{content}</Link>
}
