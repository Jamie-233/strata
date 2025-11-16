import pytest

from strata.inventory import InventoryError, parse_inventory


def test_device_overrides_group_and_group_overrides_defaults():
    devices = parse_inventory(
        {
            "defaults": {"domain": "a.lan", "ntp_servers": ["n1"]},
            "groups": {"g": {"platform": "cisco_ios", "domain": "b.lan"}},
            "devices": [{"hostname": "x", "host": "10.0.0.1", "group": "g", "ntp_servers": ["n2"]}],
        }
    )
    assert devices[0].vars["domain"] == "b.lan"
    assert devices[0].vars["ntp_servers"] == ["n2"]


def test_device_list_replaces_group_list_instead_of_appending():
    devices = parse_inventory(
        {
            "groups": {"g": {"platform": "cisco_ios", "vlans": [{"id": 10}, {"id": 20}]}},
            "devices": [{"hostname": "x", "host": "10.0.0.1", "group": "g", "vlans": [{"id": 10}]}],
        }
    )
    assert [v.id for v in devices[0].vlans] == [10]


@pytest.mark.parametrize(
    "device, fragment",
    [
        ({"host": "1.1.1.1", "platform": "cisco_ios"}, "hostname"),
        ({"hostname": "x", "platform": "juniper", "host": "1.1.1.1"}, "not supported"),
        ({"hostname": "x", "platform": "cisco_ios"}, "host"),
        ({"hostname": "x", "platform": "cisco_ios", "host": "1.1.1.1", "group": "nope"}, "not defined"),
        (
            {"hostname": "x", "platform": "cisco_ios", "host": "1.1.1.1",
             "interfaces": [{"name": "Gi0/1", "mode": "acess"}]},
            "unknown mode",
        ),
    ],
)
def test_malformed_inventory_is_rejected(device, fragment):
    with pytest.raises(InventoryError, match=fragment):
        parse_inventory({"devices": [device]})
