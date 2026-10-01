from pathlib import Path

import pytest

from strata.inventory import load_inventory, parse_inventory

EXAMPLE = Path(__file__).parent.parent / "examples" / "inventory.yml"


@pytest.fixture
def fleet():
    return load_inventory(EXAMPLE)


@pytest.fixture
def make_device():
    def _make(**overrides):
        base = {
            "hostname": "sw1",
            "platform": "cisco_ios",
            "host": "10.0.99.2",
            "vlans": [{"id": 10, "name": "DATA"}, {"id": 99, "name": "MGMT"}],
            "interfaces": [],
            "svis": [],
        }
        base.update(overrides)
        return parse_inventory({"devices": [base]})[0]

    return _make
