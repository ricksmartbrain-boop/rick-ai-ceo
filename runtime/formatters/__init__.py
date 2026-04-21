"""Channel formatters — one module per outbound channel.

Each module exposes `send(payload: dict) -> dict` and may raise
outbound_dispatcher.AuthFailure / TransientError / PermanentError.

Phase E landed these as safe scaffolds that log payload + raise
PermanentError. Enable a channel by implementing its `send` to
subprocess-call the existing backend script, then flipping
RICK_OUTBOUND_<CHANNEL>_LIVE=1 env flag. See each module for its
specific wiring.
"""
