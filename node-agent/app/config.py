from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env var names match /shared/config/.env.example exactly for the fields
    that cross a service boundary — see /shared/CONTRACT.md. SELF_URL,
    NEIGHBORS_FILE, HOST and PORT are node-agent-local additions, not part of
    that cross-service contract.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    NODE_ID: str = "regA-c1-edge1"
    REGION: str = "regA"
    CLUSTER: str = "c1"

    ADVISOR_URL: str = "http://localhost:8100"
    TRUST_URL: str = "http://localhost:8200"
    COORDINATOR_URL: str = "http://localhost:9000"

    CA_CERT_PATH: str = "../shared/certs/ca.crt"
    CLIENT_CERT_PATH: str = "./certs/node.crt"
    CLIENT_KEY_PATH: str = "./certs/node.key"

    NEIGHBORS_FILE: str = "neighbors.yaml"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Address this node advertises to peers when announcing itself via
    # POST /register. In a real multi-VM deployment, set to the node's
    # Tailscale-reachable URL.
    SELF_URL: str = "http://localhost:8000"

    # How often the outbound heartbeat loop pings each known neighbor.
    HEARTBEAT_INTERVAL_SECONDS: int = 10


settings = Settings()
