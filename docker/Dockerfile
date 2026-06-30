FROM node:24-slim

WORKDIR /app

# Install the Microsoft 365 MCP server
RUN npm install -g @softeria/ms-365-mcp-server

# Persist token cache outside the package directory
# so tokens survive npm updates
RUN mkdir -p /root/.config/ms365-mcp

ENV MS365_MCP_TOKEN_CACHE_PATH=/root/.config/ms365-mcp/.token-cache.json
ENV MS365_MCP_SELECTED_ACCOUNT_PATH=/root/.config/ms365-mcp/.selected-account.json

# Expose the HTTP port
EXPOSE 3000

# Start in HTTP mode so your Python agent can connect over localhost.
# --trust-proxy-auth: this server is only reachable internally on the
# Docker network by our own hr-app container (never exposed externally),
# so we skip the per-request Bearer-token check and use the locally
# cached MSAL identity populated by `--login` instead. Without this flag,
# --http mode requires every caller to present its own Bearer token,
# which our Python MCPClient never sends — causing 401 Unauthorized on
# every call despite a successful --login.
CMD ["npx", "@softeria/ms-365-mcp-server", "--http", "3000", "--org-mode", "--trust-proxy-auth"]