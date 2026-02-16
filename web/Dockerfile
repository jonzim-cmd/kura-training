FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_PUBLIC_MOCK_AUTH=false
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_KURA_MCP_URL
ARG NEXT_PUBLIC_SOCIAL_AUTH_BASE_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_PUBLIC_KURA_MCP_URL=${NEXT_PUBLIC_KURA_MCP_URL}
ENV NEXT_PUBLIC_SOCIAL_AUTH_BASE_URL=${NEXT_PUBLIC_SOCIAL_AUTH_BASE_URL}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_KURA_MCP_URL
ARG NEXT_PUBLIC_SOCIAL_AUTH_BASE_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_PUBLIC_KURA_MCP_URL=${NEXT_PUBLIC_KURA_MCP_URL}
ENV NEXT_PUBLIC_SOCIAL_AUTH_BASE_URL=${NEXT_PUBLIC_SOCIAL_AUTH_BASE_URL}

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/messages ./messages
COPY --from=builder /app/next.config.ts ./next.config.ts

EXPOSE 3000
CMD ["npm", "run", "start", "--", "-H", "0.0.0.0", "-p", "3000"]
