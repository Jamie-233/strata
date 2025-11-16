from strata.devices import _config_lines, push_config, run_parallel
from strata.diff import diff_configs, format_changes

RUNNING = """Building configuration...
Current configuration : 1234 bytes
! Last configuration change at 10:01:02 JST Mon Nov 3 2026
hostname sw1
!
interface GigabitEthernet1/0/1
 description old-ap
 switchport access vlan 10
 shutdown
!
interface GigabitEthernet1/0/2
 switchport access vlan 20
!
ntp clock-period 36028797
end
"""

INTENDED = """hostname sw1
!
interface GigabitEthernet1/0/2
 switchport access vlan 20
!
interface GigabitEthernet1/0/1
 description ap-lobby-01
 switchport access vlan 10
!
end
"""


def test_diff_ignores_device_noise_and_ordering():
    changes = diff_configs(RUNNING, INTENDED)
    assert len(changes) == 1
    change = changes[0]
    assert change.path == ("interface GigabitEthernet1/0/1",)
    assert sorted(change.removed) == ["description old-ap", "shutdown"]
    assert change.added == ["description ap-lobby-01"]


def test_identical_configs_have_no_changes():
    assert format_changes(diff_configs(INTENDED, INTENDED)) == "no changes"


def test_config_lines_strip_separators_and_terminators():
    assert _config_lines("hostname x\n!\ninterface Gi0/1\n shutdown\n#\nend\n") == [
        "hostname x", "interface Gi0/1", " shutdown",
    ]


class FakeConn:
    def __init__(self):
        self.sent = None
        self.saved = False
        self.closed = False

    def send_config_set(self, commands, **_):
        self.sent = commands
        return "ok"

    def save_config(self, *a, **k):
        self.saved = True
        return "saved"

    def send_command(self, command, **_):
        return command

    def disconnect(self):
        self.closed = True


def test_push_sends_commands_saves_and_always_disconnects(make_device):
    conn = FakeConn()
    push_config(make_device(), "hostname sw1\n!\nend\n", connect=lambda _d: conn)
    assert conn.sent == ["hostname sw1"]
    assert conn.saved and conn.closed


def test_one_failing_device_does_not_abort_the_batch(make_device):
    good, bad = make_device(hostname="good"), make_device(hostname="bad")

    def task(dev):
        if dev.hostname == "bad":
            raise TimeoutError("ssh timed out")
        return "done"

    results = {r.hostname: r for r in run_parallel([good, bad], task)}
    assert results["good"].ok
    assert not results["bad"].ok and "timed out" in results["bad"].output
