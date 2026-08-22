import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react-swc"
import { defineConfig } from "vite"

function getAllowedHosts() {
  const raw = process.env.VITE_ALLOWED_HOSTS
  if (!raw) return undefined
  if (raw === "true") return true

  return raw
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean)
}

const apiProxy = {
  "/api": {
    target:
      process.env.VITE_API_PROXY_TARGET ??
      `http://127.0.0.1:${process.env.BACKEND_PORT ?? "21783"}`,
    changeOrigin: true,
  },
}

// https://vitejs.dev/config/
export default defineConfig({
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return
          if (id.includes("@tanstack")) return "tanstack"
          if (id.includes("@radix-ui")) return "radix-ui"
          if (id.includes("lucide-react") || id.includes("react-icons")) {
            return "icons"
          }
          return "vendor"
        },
      },
    },
  },
  server: {
    allowedHosts: getAllowedHosts(),
    proxy: apiProxy,
  },
  preview: {
    allowedHosts: getAllowedHosts(),
    proxy: apiProxy,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  plugins: [
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
  ],
})
