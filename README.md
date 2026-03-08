# mcp2cli

Turn any MCP server or OpenAPI spec into a CLI — at runtime, with zero codegen.

Point `mcp2cli` at an OpenAPI spec URL, a local spec file, or an MCP server, and it dynamically generates a full CLI with typed flags, subcommands, and help text. No wrappers, no generated code, no per-API glue.

```
# OpenAPI
mcp2cli --spec https://api.example.com/openapi.json list-users --limit 10

# MCP over stdio
mcp2cli --mcp-stdio "npx @modelcontextprotocol/server-filesystem /tmp" list-directory --path /tmp

# MCP over HTTP/SSE
mcp2cli --mcp https://mcp.example.com/sse echo --message "hello"
```

## Why this exists

MCP servers and OpenAPI APIs both describe their capabilities in machine-readable schemas. Today, if you want to use them from a terminal, you either write bespoke CLI wrappers or use curl. mcp2cli eliminates that step — one tool talks to any of them.

### LLM context efficiency

When an LLM uses MCP tools or OpenAPI endpoints natively, every tool definition — name, description, full JSON schema — gets injected into the system prompt. This cost scales linearly with the number of tools and is paid on **every request**, whether the model uses those tools or not.

mcp2cli replaces that with a single shell command pattern. The LLM discovers tools on-demand with `--list` and `--help`, paying only for what it actually uses.

**Measured token costs** (cl100k_base tokenizer):

| Scenario | Native tool injection | mcp2cli | Reduction |
|---|--:|--:|--:|
| Small MCP server (11 tools) | 1,499 | 59 | **96%** |
| Medium OpenAPI (20 endpoints) | 3,076 | 59 | **98%** |
| Large API (50 endpoints) | 8,202 | 59 | **99%** |
| Enterprise API (200+ endpoints) | 32,808 | 59 | **>99%** |

The **59-token** mcp2cli cost is the one-time system prompt instruction telling the LLM how to use the CLI. On-demand discovery adds ~13 tokens per tool (`--list`) or ~25-50 tokens per command (`--help`), loaded only when needed — not on every turn.

**Native approach** (all tools in context on every request):
```
System prompt: "You have access to these 50 tools: [8,202 tokens of JSON schemas]"
-> 8,202 tokens consumed per request, regardless of usage
```

**mcp2cli approach** (discover on demand):
```
System prompt: "Use mcp2cli --spec <url> <command> [--flags] to call the API." (59 tokens)
-> LLM runs: mcp2cli --spec <url> --list                          (649 tokens, once)
-> LLM runs: mcp2cli --spec <url> create-pet --name Rex           (0 extra context tokens)
```

For a 50-endpoint API over a 20-turn conversation, that's **~164,000 tokens saved** (8,202 x 20 vs 59 x 20 + 649).

## Install

```bash
pip install mcp2cli

# With MCP support
pip install mcp2cli[mcp]
```

## Usage

### OpenAPI mode

```bash
# List all commands from a remote spec
mcp2cli --spec https://petstore3.swagger.io/api/v3/openapi.json --list

# Call an endpoint
mcp2cli --spec ./openapi.json --base-url https://api.example.com list-pets --status available

# With auth
mcp2cli --spec ./spec.json --auth-header "Authorization:Bearer tok_..." create-item --name "Test"

# POST with JSON body from stdin
echo '{"name": "Fido", "tag": "dog"}' | mcp2cli --spec ./spec.json create-pet --stdin

# Local YAML spec
mcp2cli --spec ./api.yaml --base-url http://localhost:8000 --list
```

### MCP stdio mode

```bash
# List tools from an MCP server
mcp2cli --mcp-stdio "npx @modelcontextprotocol/server-filesystem /tmp" --list

# Call a tool
mcp2cli --mcp-stdio "npx @modelcontextprotocol/server-filesystem /tmp" \
  read-file --path /tmp/hello.txt

# Pass environment variables to the server process
mcp2cli --mcp-stdio "node server.js" --env API_KEY=sk-... --env DEBUG=1 \
  search --query "test"
```

### MCP HTTP/SSE mode

```bash
# Connect to an MCP server over HTTP
mcp2cli --mcp https://mcp.example.com/sse --list

# With auth header
mcp2cli --mcp https://mcp.example.com/sse --auth-header "x-api-key:sk-..." \
  query --sql "SELECT 1"
```

### Output control

```bash
# Pretty-print JSON (also auto-enabled for TTY)
mcp2cli --spec ./spec.json --pretty list-pets

# Raw response body (no JSON parsing)
mcp2cli --spec ./spec.json --raw get-data

# Pipe-friendly (compact JSON when not a TTY)
mcp2cli --spec ./spec.json list-pets | jq '.[] | .name'
```

### Caching

Specs and MCP tool lists are cached in `~/.cache/mcp2cli/` with a 1-hour TTL by default.

```bash
# Force refresh
mcp2cli --spec https://api.example.com/spec.json --refresh --list

# Custom TTL (seconds)
mcp2cli --spec https://api.example.com/spec.json --cache-ttl 86400 --list

# Custom cache key
mcp2cli --spec https://api.example.com/spec.json --cache-key my-api --list

# Override cache directory
MCP2CLI_CACHE_DIR=/tmp/my-cache mcp2cli --spec ./spec.json --list
```

Local file specs are never cached.

## CLI reference

```
mcp2cli [global options] <subcommand> [command options]

Source (mutually exclusive, one required):
  --spec URL|FILE       OpenAPI spec (JSON or YAML, local or remote)
  --mcp URL             MCP server URL (HTTP/SSE)
  --mcp-stdio CMD       MCP server command (stdio transport)

Options:
  --auth-header K:V     HTTP header (repeatable)
  --base-url URL        Override base URL from spec
  --env KEY=VALUE       Env var for MCP stdio server (repeatable)
  --cache-key KEY       Custom cache key
  --cache-ttl SECONDS   Cache TTL (default: 3600)
  --refresh             Bypass cache
  --list                List available subcommands
  --pretty              Pretty-print JSON output
  --raw                 Print raw response body
  --version             Show version
```

Subcommands and their flags are generated dynamically from the spec or MCP server tool definitions. Run `<subcommand> --help` for details.

## How it works

1. **Load** -- Fetch the OpenAPI spec or connect to the MCP server. Resolve `$ref`s. Cache for reuse.
2. **Extract** -- Walk the spec paths/tools and produce a uniform list of command definitions with typed parameters.
3. **Build** -- Generate an argparse parser with subcommands, flags, types, choices, and help text.
4. **Execute** -- Dispatch the parsed args as an HTTP request (OpenAPI) or tool call (MCP).

Both adapters produce the same internal `CommandDef` structure, so the CLI builder and output handling are shared.

## Development

```bash
# Install with test + MCP deps
uv sync --extra test --extra mcp

# Run tests (88 tests covering OpenAPI, MCP stdio, MCP HTTP, and caching)
uv run pytest tests/ -v

# Run just the cache integration tests
uv run pytest tests/test_cache.py -v
```

## License

MIT
