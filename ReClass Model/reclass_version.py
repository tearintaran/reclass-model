"""Application distribution version.

This is deliberately separate from ``engine.config.ENGINE_VERSION``. The package
and API can evolve without changing a clinically reviewed scoring configuration,
while scoring changes continue to alter the engine/config version and
reconstruction hashes.
"""

__version__ = "0.1.0"
