from strata.inventory import parse_inventory
from strata.validate import Severity, validate


def messages(issues, severity=Severity.ERROR):
    return [i.message for i in issues if i.severity is severity]


def test_example_fleet_is_clean(fleet):
    assert messages(validate(fleet)) == []


def test_access_port_on_undefined_vlan(make_device):
    dev = make_device(interfaces=[{"name": "Gi0/1", "mode": "access", "vlan": 30}])
    assert any("access VLAN 30 is not defined" in m for m in messages(validate([dev])))


def test_ios_reserved_vlan(make_device):
    dev = make_device(vlans=[{"id": 1003}])
    assert any("reserved" in m for m in messages(validate([dev])))


def test_native_vlan_carried_tagged_is_a_warning(make_device):
    dev = make_device(
        interfaces=[{"name": "Gi0/48", "mode": "trunk", "native_vlan": 10, "allowed_vlans": [10, 99]}]
    )
    issues = validate([dev])
    assert messages(issues) == []
    assert any("native VLAN 10" in m for m in messages(issues, Severity.WARNING))


def test_overlapping_subnets_on_one_device(make_device):
    dev = make_device(
        vlans=[{"id": 10}, {"id": 20}],
        svis=[{"vlan": 10, "ip": "10.0.0.1/22"}, {"vlan": 20, "ip": "10.0.2.1/24"}],
    )
    assert any("overlaps" in m for m in messages(validate([dev])))


def test_unreachable_default_gateway(make_device):
    dev = make_device(svis=[{"vlan": 99, "ip": "10.0.99.2/24"}], default_gateway="10.0.98.1")
    assert any("never be resolved" in m for m in messages(validate([dev])))


def test_duplicate_address_across_devices():
    devices = parse_inventory(
        {
            "devices": [
                {"hostname": "a", "platform": "cisco_ios", "host": "10.0.0.1",
                 "vlans": [{"id": 10}], "svis": [{"vlan": 10, "ip": "10.0.10.1/24"}]},
                {"hostname": "b", "platform": "huawei_vrp", "host": "10.0.0.2",
                 "vlans": [{"id": 10}], "svis": [{"vlan": 10, "ip": "10.0.10.1/24"}]},
            ]
        }
    )
    assert any("10.0.10.1 is also used by" in m for m in messages(validate(devices)))


def test_mgmt_ip_equal_to_own_svi_is_fine(make_device):
    dev = make_device(svis=[{"vlan": 99, "ip": "10.0.99.2/24"}])
    assert messages(validate([dev])) == []
