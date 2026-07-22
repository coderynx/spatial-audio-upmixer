"""Development and container entry point for Upmixer Web."""

from __future__ import annotations

import os


def main() -> None:
    """Run the API with reverse-proxy header support."""
    import uvicorn

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
