"""Development and container entry point for Upmixer Web."""

from __future__ import annotations

import logging
import os


def main() -> None:
    """Run the API with reverse-proxy header support."""
    import uvicorn

    # uvicorn.run only configures its own "uvicorn.*" loggers, leaving root
    # (and this app's/core's plain-named loggers, e.g. "upmixer",
    # "upmixer_web") without a handler — pipeline progress and errors from
    # in-process work (e.g. WorkerManager.prepare_reference_match) are
    # otherwise silently dropped instead of reaching stdout.
    logging.basicConfig(
        level=os.getenv("UPMIXER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s [%(process)d]: %(message)s",
    )

    uvicorn.run(
        "upmixer_web.api:create_app",
        factory=True,
        host=os.getenv("UPMIXER_HOST", "0.0.0.0"),
        port=int(os.getenv("UPMIXER_PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("UPMIXER_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
