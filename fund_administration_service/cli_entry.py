"""Console-script entry point — delegates to ``ServiceBootstrap.run()``."""

from __future__ import annotations

from fund_administration_service.bootstrap import build_service_bootstrap


def main() -> None:
    """Invoke the ``ServiceBootstrap`` dispatcher."""

    build_service_bootstrap().run()


if __name__ == "__main__":
    main()
