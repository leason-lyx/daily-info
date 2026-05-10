from dataclasses import dataclass


DEFAULT_PROFILE_ID = "default"


@dataclass(frozen=True)
class ProfileContext:
    profile_id: str = DEFAULT_PROFILE_ID


DEFAULT_PROFILE = ProfileContext()
