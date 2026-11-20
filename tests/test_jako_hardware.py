"""Hulk's hardware warnings: battery, battery wear, disk space, SSD health, memory."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from jako import hands as H
from jako import scheduler as S
from jako.config import JakoConfig
from jako.memory import Memory


def keys(f):
    return [k for k, _ in H.hardware_check(f)[1]]


def test_healthy_laptop_has_no_warnings():
    f = {"battery_pct": 80, "plugged": True, "design_mwh": 50000, "full_mwh": 47000, "cycles": 120,
         "disk_free_gb": 74, "disk_total_gb": 256, "memory_pct": 60, "memory_total_gb": 8,
         "disks": [{"Name": "NVMe SSD", "Media": "SSD", "Health": "Healthy", "Wear": 5, "Temp": 41}]}
    lines, warn = H.hardware_check(f)
    assert warn == []
    assert any("Battery health: 94%" in l for l in lines) and any("5% worn" in l for l in lines)


def test_low_battery_only_when_unplugged():
    assert keys({"battery_pct": 15, "plugged": False}) == ["battery_low"]
    assert keys({"battery_pct": 15, "plugged": True}) == []
    assert "now" in H.hardware_check({"battery_pct": 8, "plugged": False})[1][0][1]


def test_battery_wear_says_when_to_replace():
    plan = H.hardware_check({"battery_pct": 90, "plugged": True, "design_mwh": 50000, "full_mwh": 37000})[1]
    assert plan[0][0] == "battery_wear" and "Plan a battery replacement" in plan[0][1]
    now = H.hardware_check({"battery_pct": 90, "plugged": True, "design_mwh": 50000, "full_mwh": 28000})[1]
    assert "time to replace the battery" in now[0][1]


def test_disk_and_drive_warnings():
    assert keys({"disk_free_gb": 8, "disk_total_gb": 256}) == ["disk_full"]
    assert keys({"disk_free_gb": 18, "disk_total_gb": 256}) == ["disk_low"]
    assert keys({"disks": [{"Name": "SSD", "Health": "Warning"}]}) == ["ssd_health"]
    assert keys({"disks": [{"Name": "SSD", "Health": "Healthy", "Wear": 85}]}) == ["ssd_wear"]
    assert keys({"disks": [{"Name": "SSD", "Health": "Healthy", "Temp": 75}]}) == ["ssd_hot"]


def test_battery_report_is_read_from_windows_xml(monkeypatch, tmp_path):
    xml = ('<?xml version="1.0"?><BatteryReport xmlns="http://schemas.microsoft.com/battery/2012"><Batteries>'
           '<Battery><DesignCapacity>41998</DesignCapacity><FullChargeCapacity>33012</FullChargeCapacity>'
           '<CycleCount>412</CycleCount></Battery></Batteries></BatteryReport>')
    monkeypatch.setattr(H.tempfile if hasattr(H, "tempfile") else __import__("tempfile"), "gettempdir", lambda: str(tmp_path))

    def fake_run(cmd, **kw):
        (tmp_path / "jako-battery-report.xml").write_text(xml)
        return SimpleNamespace(stdout="", returncode=0)
    monkeypatch.setattr(H.subprocess, "run", fake_run)
    assert H._battery_report() == {"design_mwh": 41998, "full_mwh": 33012, "cycles": 412}


def test_hardware_health_text_has_warning_lines(monkeypatch):
    monkeypatch.setattr(H, "hardware_facts", lambda: {"battery_pct": 12, "plugged": False, "disk_free_gb": 70, "disk_total_gb": 256})
    text = H.hardware_health()
    assert "WARNING [battery_low]: Battery is low, 12%" in text


class FakeHands:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def online(self):
        return True

    def do(self, action, args):
        assert action == "hardware_health"
        self.calls += 1
        return {"text": self.text}


@pytest.fixture
def brain(tmp_path):
    cfg = JakoConfig(api_key="t", data_dir=tmp_path, hands_token="t" * 32)
    hands = FakeHands("Battery: 15% on battery.\nWARNING [battery_low]: Battery is low, 15%. Please plug in the charger.\n"
                      "WARNING [battery_wear]: The battery has worn down to 74% of its original capacity. Plan a battery replacement in the next months.")
    return SimpleNamespace(cfg=cfg, mem=Memory(tmp_path / "j.db"), team=SimpleNamespace(hands=hands))


class Tg:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def test_hulk_warns_out_loud_and_does_not_repeat_too_often(brain):
    tg = Tg()
    said = S.hardware_alerts(brain, tg, clock=1000)
    assert len(said) == 2 and len(tg.sent) == 2 and "Hulk" in tg.sent[0]
    assert S.hardware_alerts(brain, tg, clock=1000 + 60) == []                   # checks only every 15 minutes
    again = S.hardware_alerts(brain, tg, clock=1000 + 25 * 60)                  # low battery again after 20 min,
    assert again == ["Battery is low, 15%. Please plug in the charger."]       # battery wear only weekly
    outbox = json.dumps(brain.mem.__dict__, default=str)                        # spoken on the Jako screen too
    assert brain.team.hands.calls == 2


def test_memory_always_full_gets_an_upgrade_tip(brain, tmp_path):
    brain.team.hands.text = "No hardware warnings."
    now = datetime.now()
    rows = ["time,cpu,ram,disk_free_gb,battery,plugged,top_memory_app"] + [
        f"{(now - timedelta(minutes=10 * i)).strftime('%Y-%m-%d %H:%M')},50,91,70,100,1,x" for i in range(15)]
    (tmp_path / "perf.csv").write_text("\n".join(rows))
    said = S.hardware_alerts(brain, Tg(), clock=5000)
    assert len(said) == 1 and "more RAM" in said[0]


def test_hulk_has_the_hardware_tool():
    from jako.team import HANDS_TOOLS, LAPTOP_TOOLS
    assert "hardware_health" in HANDS_TOOLS["hulk"] and "hardware_health" in H.SAFE
    assert any(t["name"] == "hardware_health" for t in LAPTOP_TOOLS)
