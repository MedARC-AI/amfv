interface AuthLayoutProps {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <div className="relative hidden bg-primary lg:flex lg:flex-col lg:items-center lg:justify-center lg:gap-6 lg:p-10">
        <img
          alt="MedARC"
          className="h-16 w-auto"
          src="/medarc-logo-horizontal-white.webp"
        />
        <span className="text-center text-xl font-semibold tracking-normal text-primary-foreground">
          Agentic Medical Fact Verifier Website
        </span>
      </div>
      <div className="flex flex-col gap-4 p-6 md:p-10">
        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-xs">{children}</div>
        </div>
      </div>
    </div>
  )
}
