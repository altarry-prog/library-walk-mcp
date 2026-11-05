"""FabriX hosted entry point for 「도서관 산책 읽기전용」.

Zero-configuration defaults for platform hosting:
- binds 0.0.0.0 on $PORT (default 8080, the FabriX Repository screen's default Port Number); transport from $MCP_TRANSPORT (streamable-http at /mcp, sse at /sse, or stdio)
- auth: Bearer check only when $MCP_ACCESS_TOKEN is set; otherwise platform-managed (FabriX controls ingress)
- Host header allowlist only when $MCP_ALLOWED_HOSTS is set
- mock catalog, live provider calls OFF, no write/booking tools, no secrets in this repository
"""
from server import build_runtime, serve

if __name__ == '__main__':
    serve(build_runtime(hosted=True), default_host='0.0.0.0', proxy_headers=True, default_port=8080)
