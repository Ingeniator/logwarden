"""Logs through logwarden.logging.configure(callsite=True) for the CI `sarif` job.

Its findings go to code scanning on these lines. One is known on main (status=), so a pull
request that adds a bad log call shows a new alert on its own line in the diff.
"""
from logwarden.logging import configure

log = configure(service_name="orders-api", service_version="1.0.0", environment="test", callsite=True)


def create_order(order_id: int) -> None:
    log.info("order_created", **{"order.id": order_id})
    log.info("order_paid", status=200)          # known finding: banned synonym


if __name__ == "__main__":
    create_order(42)
