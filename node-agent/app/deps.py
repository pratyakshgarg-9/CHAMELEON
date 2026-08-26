from fastapi import Request

from app.neighbors import PeerRegistry


def get_registry(request: Request) -> PeerRegistry:
    return request.app.state.registry
