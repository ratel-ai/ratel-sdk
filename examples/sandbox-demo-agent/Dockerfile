# The image runs one bundled file: `pnpm build` writes dist/agent.cjs first.
FROM node:24-slim
WORKDIR /app
ENV NODE_ENV=production
COPY dist/agent.cjs ./agent.cjs
USER node
EXPOSE 8080
CMD ["node", "agent.cjs"]
