# Deploying the broker

The `robotsix-broker` is a **standalone, long-running daemon** that
provides agent registration, discovery, and message-routing services to
the robotsix ecosystem.  This guide covers configuration, TLS
provisioning, and containerised deployment.

See [ADR 0006](../decisions/0006-broker-server-architecture.md) for the
architecture and security model.

---

## Quickstart (Docker Compose)

```bash
# 1. Generate TLS material (self-signed CA + server cert)
mkdir -p tls
openssl req -x509 -newkey rsa:4096 -keyout tls/ca.key -out tls/ca.pem \
  -days 365 -nodes -subj "/CN=robotsix-ca"
openssl req -newkey rsa:2048 -keyout tls/server.key -out tls/server.csr \
  -nodes -subj "/CN=localhost"
openssl x509 -req -in tls/server.csr -CA tls/ca.pem -CAkey tls/ca.key \
  -CAcreateserial -out tls/server.pem -days 365

# 2. Create an agent-tokens file
echo '{"agent-a": "tok-a", "agent-b": "tok-b"}' > agent-tokens.json

# 3. Review and adjust .env.broker (copy from .env.broker.example)
cp .env.broker.example .env.broker
# Edit .env.broker — at minimum set:
#   ROBOTSIX_BROKER_TLS_CERT=/etc/robotsix/tls/server.pem
#   ROBOTSIX_BROKER_TLS_KEY=/etc/robotsix/tls/server.key
#   ROBOTSIX_BROKER_AGENT_TOKENS_FILE=/etc/robotsix/broker/agent-tokens.json

# 4. Build and start
docker compose up --build
```

The broker listens on `https://localhost:8443`.

---

## Environment variables

Every variable uses the `ROBOTSIX_BROKER_` prefix (ADR 0006 §3.1).

### Listen address

| Variable | Meaning | Default |
|---|---|---|
| `ROBOTSIX_BROKER_HOST` | Bind host address | `0.0.0.0` |
| `ROBOTSIX_BROKER_PORT` | TCP port (int) | `8443` |

### Mode

| Variable | Meaning | Default |
|---|---|---|
| `ROBOTSIX_BROKER_ENV` | `"production"` or `"development"` | `"production"` |

**Production** mode enforces TLS and per-agent authentication (see
[Security model](#security-model)).  **Development** mode relaxes those
requirements but emits loud warnings so an insecure boot is never
silent.

### TLS

| Variable | Meaning | Default |
|---|---|---|
| `ROBOTSIX_BROKER_TLS_CERT` | Path to server certificate PEM | unset |
| `ROBOTSIX_BROKER_TLS_KEY` | Path to server private-key PEM | unset |
| `ROBOTSIX_BROKER_TLS_CA` | Path to CA bundle PEM (mTLS) | unset |
| `ROBOTSIX_BROKER_REQUIRE_CLIENT_CERT` | Enable mTLS (`1`/`true`/`yes`) | `false` |

When `REQUIRE_CLIENT_CERT` is truthy, `TLS_CA` **must** be set and the
file must exist — mutual TLS needs a trust anchor.

### Agent credentials

| Variable | Meaning | Default |
|---|---|---|
| `ROBOTSIX_BROKER_AGENT_TOKENS_FILE` | Path to JSON file `{"agent_id": "token", ...}` | unset |
| `ROBOTSIX_BROKER_AGENT_TOKENS` | Inline fallback `id=token,id=token` | unset |

**Precedence:** when both are set, `AGENT_TOKENS_FILE` wins.

The JSON file format:

```json
{
  "agent-1": "shared-secret-one",
  "agent-2": "shared-secret-two"
}
```

The inline format:

```
agent-1=shared-secret-one,agent-2=shared-secret-two
```

### Tuning

| Variable | Meaning | Default |
|---|---|---|
| `ROBOTSIX_BROKER_TTL_SECONDS` | Registration TTL (int) | server default (60) |
| `ROBOTSIX_BROKER_RATE_LIMIT` | Max requests/agent/second (float; 0 = off) | server default (0) |
| `ROBOTSIX_BROKER_MAX_BODY_SIZE` | Max HTTP body bytes (int) | server default (1 MiB) |

### Audit

| Variable | Meaning | Default |
|---|---|---|
| `ROBOTSIX_BROKER_AUDIT_LOG` | Path to JSON-lines audit file | stdout |

---

## TLS certificate provisioning

The broker requires a **server certificate** and **private key** for
TLS.  For mutual TLS (mTLS) you also need a **CA bundle** that can
validate client certificates.

### Self-signed (development / testing)

```bash
mkdir -p tls
openssl req -x509 -newkey rsa:4096 \
  -keyout tls/server.key -out tls/server.pem \
  -days 365 -nodes -subj "/CN=localhost"
```

Then set:

```
ROBOTSIX_BROKER_TLS_CERT=./tls/server.pem
ROBOTSIX_BROKER_TLS_KEY=./tls/server.key
ROBOTSIX_BROKER_ENV=development
```

### Production (Let's Encrypt or internal PKI)

Mount the certificate and key into the container (e.g. via Docker
secrets or a read-only bind mount) and point the env vars at the
mounted paths.

---

## Agent credential provisioning

Create a JSON file mapping agent identifiers to their bearer tokens:

```json
{
  "calendar-agent": "s3cret-cal",
  "auto-mail": "s3cret-mail"
}
```

Mount it into the container and set:

```
ROBOTSIX_BROKER_AGENT_TOKENS_FILE=/etc/robotsix/broker/agent-tokens.json
```

---

## Production vs development

| Feature | Production (`ROBOTSIX_BROKER_ENV=production`) | Development (`ROBOTSIX_BROKER_ENV=development`) |
|---|---|---|
| TLS | **Required** — missing cert/key aborts startup | Allowed to be absent; warning is emitted |
| Agent auth | **Required** — at least one token pair | Allowed to be absent; warning is emitted |
| Plaintext / anonymous | **Never reachable** | Reachable with loud warnings |

The security invariant: **in production, no plaintext or anonymous-auth
code path is reachable**.  The `BrokerConfig` validator enforces this
at startup — it raises `ValueError` (converted to a non-zero exit code
by the entrypoint) before the server socket is created.

---

## Running directly (without Docker)

```bash
# Set env vars, then:
python -m robotsix_agent_comm.broker
# or
robotsix-broker
```

---

## Docker image

```bash
docker build -t robotsix-broker .
docker run --rm -p 8443:8443 \
  -v "$(pwd)/tls:/etc/robotsix/tls:ro" \
  -v "$(pwd)/agent-tokens.json:/etc/robotsix/broker/agent-tokens.json:ro" \
  -e ROBOTSIX_BROKER_TLS_CERT=/etc/robotsix/tls/server.pem \
  -e ROBOTSIX_BROKER_TLS_KEY=/etc/robotsix/tls/server.key \
  -e ROBOTSIX_BROKER_AGENT_TOKENS_FILE=/etc/robotsix/broker/agent-tokens.json \
  robotsix-broker
```
