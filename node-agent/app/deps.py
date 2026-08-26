from fastapi import Request

from app.election import ElectionState
from app.neighbors import PeerRegistry


def get_registry(request: Request) -> PeerRegistry:
    return request.app.state.registry


def get_election_state(request: Request) -> ElectionState:
    return request.app.state.election
