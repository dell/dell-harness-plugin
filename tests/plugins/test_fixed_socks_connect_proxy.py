import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/fixed_socks_connect_proxy.py"


def load_proxy():
    spec = importlib.util.spec_from_file_location("fixed_socks_connect_proxy", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_address_accepts_valid_host_and_port():
    proxy = load_proxy()
    assert proxy.address("127.0.0.1:18080") == ("127.0.0.1", 18080)


@pytest.mark.parametrize(
    "value",
    ["missing-port", "127.0.0.1:not-a-port", "127.0.0.1:0", "127.0.0.1:65536"],
)
def test_address_rejects_invalid_values(value):
    proxy = load_proxy()
    with pytest.raises(Exception):
        proxy.address(value)


def test_mapping_requires_a_literal_ipv4_target_and_normalizes_host():
    proxy = load_proxy()
    assert proxy.host_mapping("DDAE.EXAMPLE=192.0.2.10") == (
        "ddae.example",
        "192.0.2.10",
    )
    with pytest.raises(Exception):
        proxy.host_mapping("ddae.example=not-an-ip")
