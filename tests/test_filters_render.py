import pytest
from jinja2 import UndefinedError

from strata.filters import netmask, vlan_ranges, wildcard
from strata.render import build_environment, render_device, vrp_vlan_list


@pytest.mark.parametrize(
    "vlans, expected",
    [([], ""), ([10], "10"), ([10, 11, 12, 20], "10-12,20"), ([20, 10, 11, 11], "10-11,20")],
)
def test_vlan_ranges(vlans, expected):
    assert vlan_ranges(vlans) == expected


def test_vrp_uses_its_own_range_grammar():
    assert vrp_vlan_list([10, 11, 12, 20]) == "10 to 12 20"


def test_wildcard_is_bitwise_inverse_not_octet_swap():
    assert wildcard("10.0.4.0/22") == "0.0.3.255"
    assert netmask("10.0.4.1/22") == "255.255.252.0"


def test_ios_render_contains_expected_blocks(fleet):
    lobby = next(d for d in fleet if d.hostname == "acc-lobby-01")
    out = render_device(lobby)
    assert "switchport trunk allowed vlan 10,20,99" in out
    assert "switchport trunk native vlan 999" in out
    assert "spanning-tree bpduguard enable" in out
    assert "ip default-gateway 10.10.99.1" in out
    assert "switchport trunk encapsulation" not in out


def test_vrp_render_uses_vrp_syntax(fleet):
    core = next(d for d in fleet if d.hostname == "core-01")
    out = render_device(core)
    assert "vlan batch 10 20 30 99 999" in out
    assert "interface Vlanif20" in out
    assert "ip address 10.10.20.1 22" in out
    assert "dhcp relay server-ip 10.10.0.67" in out
    assert "ip route-static 0.0.0.0 0.0.0.0 203.0.113.1" in out


def test_missing_template_variable_fails_loudly(tmp_path, make_device):
    (tmp_path / "cisco_ios.j2").write_text("snmp-server location {{ vars.site_location }}\n")
    env = build_environment([tmp_path])
    with pytest.raises(UndefinedError):
        render_device(make_device(), env)
