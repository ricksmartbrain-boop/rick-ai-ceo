#!/usr/bin/env bash
# Website deployment helper — scaffold, deploy, and manage Vercel sites.
#
# Usage:
#   deploy-site.sh --init <name>              # Scaffold new Next.js project
#   deploy-site.sh --deploy <path> [--preview] # Deploy to Vercel
#   deploy-site.sh --status <name>            # Check deployment status
#   deploy-site.sh --domains <name>           # List custom domains
#   deploy-site.sh --logs <name>              # Tail recent logs

set -euo pipefail

ACTION=""
NAME=""
DEPLOY_PATH=""
PREVIEW=false
TEMPLATE_DIR="$(cd "$(dirname "$0")/../templates" && pwd)"

usage() {
    cat <<EOF
Website Deploy Tool

Usage:
  deploy-site.sh --init <name>                Scaffold new Next.js project from minimal template
  deploy-site.sh --deploy <path> [--preview]  Deploy to Vercel (production or preview)
  deploy-site.sh --status <name>              Check deployment status
  deploy-site.sh --domains <name>             List custom domains for project
  deploy-site.sh --logs <name>                Tail recent deployment logs

Environment:
  VERCEL_TOKEN       Vercel API token (required for deploy/status/domains/logs)
  VERCEL_ORG_ID      Vercel team/org ID (optional)

Examples:
  deploy-site.sh --init my-saas
  deploy-site.sh --deploy ./my-saas
  deploy-site.sh --deploy ./my-saas --preview
  deploy-site.sh --status my-saas
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --init)
            ACTION="init"
            NAME="${2:-}"
            [[ -z "$NAME" ]] && { echo "Error: --init requires a project name"; exit 1; }
            shift 2
            ;;
        --deploy)
            ACTION="deploy"
            DEPLOY_PATH="${2:-}"
            [[ -z "$DEPLOY_PATH" ]] && { echo "Error: --deploy requires a path"; exit 1; }
            shift 2
            ;;
        --preview)
            PREVIEW=true
            shift
            ;;
        --status)
            ACTION="status"
            NAME="${2:-}"
            [[ -z "$NAME" ]] && { echo "Error: --status requires a project name"; exit 1; }
            shift 2
            ;;
        --domains)
            ACTION="domains"
            NAME="${2:-}"
            [[ -z "$NAME" ]] && { echo "Error: --domains requires a project name"; exit 1; }
            shift 2
            ;;
        --logs)
            ACTION="logs"
            NAME="${2:-}"
            [[ -z "$NAME" ]] && { echo "Error: --logs requires a project name"; exit 1; }
            shift 2
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run with --help for usage."
            exit 1
            ;;
    esac
done

[[ -z "$ACTION" ]] && usage

echo "======================================="
echo "  Website Builder -- Deploy"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "======================================="
echo ""

case $ACTION in
    init)
        echo "Scaffolding new project: $NAME"
        echo "---"

        if [[ -d "$NAME" ]]; then
            echo "Error: Directory '$NAME' already exists."
            exit 1
        fi

        # Copy minimal template as starting point
        if [[ -d "$TEMPLATE_DIR/landing-minimal" ]]; then
            mkdir -p "$NAME/app"
            cp "$TEMPLATE_DIR/landing-minimal/package.json" "$NAME/package.json"
            cp "$TEMPLATE_DIR/landing-minimal/page.tsx" "$NAME/app/page.tsx"

            # Create basic Next.js config
            cat > "$NAME/next.config.js" <<'NEXTCFG'
/** @type {import('next').NextConfig} */
const nextConfig = {};
module.exports = nextConfig;
NEXTCFG

            # Create tailwind config
            cat > "$NAME/tailwind.config.ts" <<'TAILCFG'
import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: { extend: {} },
  plugins: [],
};

export default config;
TAILCFG

            # Create global CSS
            mkdir -p "$NAME/app"
            cat > "$NAME/app/globals.css" <<'CSS'
@tailwind base;
@tailwind components;
@tailwind utilities;
CSS

            # Create layout
            cat > "$NAME/app/layout.tsx" <<'LAYOUT'
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "{{PRODUCT_NAME}}",
  description: "{{HEADLINE}}",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
LAYOUT

            # Create postcss config
            cat > "$NAME/postcss.config.js" <<'POSTCSS'
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
POSTCSS

            # Create tsconfig
            cat > "$NAME/tsconfig.json" <<'TSCONFIG'
{
  "compilerOptions": {
    "target": "es5",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
TSCONFIG

            echo "Project scaffolded at ./$NAME"
            echo ""
            echo "Next steps:"
            echo "  cd $NAME"
            echo "  pnpm install"
            echo "  # Edit app/page.tsx — replace {{PLACEHOLDERS}}"
            echo "  pnpm dev"
        else
            echo "Error: Template directory not found at $TEMPLATE_DIR/landing-minimal"
            exit 1
        fi
        ;;

    deploy)
        echo "Deploying: $DEPLOY_PATH"
        echo "---"

        if ! command -v vercel &> /dev/null; then
            echo "Error: vercel CLI not installed."
            echo "Install: npm i -g vercel"
            exit 1
        fi

        if [[ -z "${VERCEL_TOKEN:-}" ]]; then
            echo "Warning: VERCEL_TOKEN not set. You may be prompted to log in."
        fi

        if [[ ! -d "$DEPLOY_PATH" ]]; then
            echo "Error: Path '$DEPLOY_PATH' does not exist."
            exit 1
        fi

        VERCEL_ARGS=()
        if [[ -n "${VERCEL_TOKEN:-}" ]]; then
            VERCEL_ARGS+=(--token "$VERCEL_TOKEN")
        fi

        if [[ "$PREVIEW" == "true" ]]; then
            echo "Mode: Preview"
            vercel "$DEPLOY_PATH" "${VERCEL_ARGS[@]}" --yes
        else
            echo "Mode: Production"
            vercel "$DEPLOY_PATH" --prod "${VERCEL_ARGS[@]}" --yes
        fi

        echo ""
        echo "Deployment complete."
        ;;

    status)
        echo "Deployment Status: $NAME"
        echo "---"

        if ! command -v vercel &> /dev/null; then
            echo "Error: vercel CLI not installed."
            exit 1
        fi

        VERCEL_ARGS=()
        if [[ -n "${VERCEL_TOKEN:-}" ]]; then
            VERCEL_ARGS+=(--token "$VERCEL_TOKEN")
        fi

        vercel ls "$NAME" "${VERCEL_ARGS[@]}" 2>/dev/null || echo "Could not fetch status for '$NAME'."
        ;;

    domains)
        echo "Custom Domains: $NAME"
        echo "---"

        if ! command -v vercel &> /dev/null; then
            echo "Error: vercel CLI not installed."
            exit 1
        fi

        VERCEL_ARGS=()
        if [[ -n "${VERCEL_TOKEN:-}" ]]; then
            VERCEL_ARGS+=(--token "$VERCEL_TOKEN")
        fi

        vercel domains ls "${VERCEL_ARGS[@]}" 2>/dev/null || echo "Could not fetch domains."
        ;;

    logs)
        echo "Recent Logs: $NAME"
        echo "---"

        if ! command -v vercel &> /dev/null; then
            echo "Error: vercel CLI not installed."
            exit 1
        fi

        VERCEL_ARGS=()
        if [[ -n "${VERCEL_TOKEN:-}" ]]; then
            VERCEL_ARGS+=(--token "$VERCEL_TOKEN")
        fi

        vercel logs "$NAME" "${VERCEL_ARGS[@]}" 2>/dev/null || echo "Could not fetch logs for '$NAME'."
        ;;
esac

echo ""
echo "======================================="
