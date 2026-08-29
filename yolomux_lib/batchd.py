"""Canonical entry point for YOLOmux's asynchronous batch broker."""

from .infra import jobd as _implementation

BatchClient = _implementation.BatchClient
PersistentJobBroker = _implementation.PersistentJobBroker
BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS = _implementation.BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS
BATCHD_PROTOCOL_VERSION = _implementation.BATCHD_PROTOCOL_VERSION
BATCHD_SERVICE_NAME = _implementation.BATCHD_SERVICE_NAME


def main(argv=None):
    return _implementation.main(argv, service_name="batchd")


if __name__ == "__main__":
    raise SystemExit(main())
