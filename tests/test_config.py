"""Configuration loading, validation messages, round trip."""

import copy

import pytest
import yaml

from voicechain.config import ConfigError, default_config_path, load_config, save_config, validate_config
from voicechain.dsp import STAGE_ORDER, Chain


@pytest.fixture
def cfg():
    return load_config(default_config_path())


def test_default_config_loads(cfg):
    assert default_config_path().is_file()
    assert cfg["sample_rate"] == 48000 and cfg["block_size"] == 256
    assert set(cfg["chain"]) == set(STAGE_ORDER)
    assert len(cfg["chain"]["eq"]["bands"]) == 3
    assert isinstance(Chain.from_config(cfg), Chain)


def test_round_trip_is_identical(cfg, tmp_path):
    path = tmp_path / "sub" / "out.yaml"
    save_config(cfg, path)
    reloaded = load_config(path)
    assert reloaded == cfg
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#") and "RØDE" in text


def test_default_yaml_documents_every_value(cfg):
    """Every leaf of the default file carries a comment (Beauftragung 3.1)."""
    lines = default_config_path().read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.endswith(":"):
            continue
        assert "#" in line, f"undocumented value: {line!r}"


@pytest.mark.parametrize("mutate, fragment", [
    (lambda c: c["chain"]["limiter"].pop("release_ms"), "chain.limiter.release_ms: missing"),
    (lambda c: c["chain"]["expander"].update(threshold_db="loud"), "chain.expander.threshold_db: must be a number"),
    (lambda c: c["chain"]["compressor"].update(ratio=99.0), "chain.compressor.ratio: value 99.0 outside"),
    (lambda c: c["chain"]["highpass"].update(enabled="yes"), "chain.highpass.enabled: must be true or false"),
    (lambda c: c["chain"]["highpass"].update(order=4.5), "chain.highpass.order: must be an integer"),
    (lambda c: c["chain"]["eq"]["bands"][0].update(type="notch"), "chain.eq.bands[0].type='notch'"),
    (lambda c: c["chain"]["eq"]["bands"][1].pop("q"), "chain.eq.bands[1]: missing key 'q'"),
    (lambda c: c["chain"]["deesser"].update(colour=1.0), "chain.deesser.colour: unknown key"),
    (lambda c: c.update(sample_rate=48000.0), "sample_rate: must be an integer"),
    (lambda c: c.update(block_size=7), "block_size: value 7 outside"),
    (lambda c: c["input"].update(device=""), "input.device: must be a non-empty string"),
    (lambda c: c["adaptive"]["bounds"].update({"expander.threshold_db": [-30.0, -70.0]}), "min -30.0 must be smaller"),
    (lambda c: c["adaptive"]["bounds"].update({"reverb.mix": [0.0, 1.0]}), "adaptive.bounds.reverb.mix"),
    (lambda c: c["adaptive"]["bounds"].update({"compressor.threshold_db": [-10.0, -5.0]}), "current value chain.compressor.threshold_db=-20.0 lies outside"),
    (lambda c: c["chain"]["normalizer"].update(min_gain_db=5.0, max_gain_db=2.0), "min_gain_db: must not exceed max_gain_db"),
    (lambda c: c.pop("adaptive"), "adaptive: missing"),
    (lambda c: c.update(extra=1), "extra: unknown key"),
])
def test_validation_messages(cfg, mutate, fragment):
    bad = copy.deepcopy(cfg)
    mutate(bad)
    with pytest.raises(ConfigError) as info:
        validate_config(bad)
    assert fragment in str(info.value)


def test_all_errors_are_reported_together(cfg):
    bad = copy.deepcopy(cfg)
    bad["chain"]["expander"]["threshold_db"] = "x"
    del bad["chain"]["limiter"]["release_ms"]
    bad["unknown"] = True
    with pytest.raises(ConfigError) as info:
        validate_config(bad)
    message = str(info.value)
    assert message.count("\n  - ") == 3


def test_optional_internal_parameters_are_accepted(cfg):
    extended = copy.deepcopy(cfg)
    extended["chain"]["expander"]["detector_ms"] = 8.0
    extended["chain"]["limiter"]["true_peak_margin_db"] = 1.0
    validate_config(extended)
    chain = Chain.from_config(extended)
    assert chain.get_param("expander", "detector_ms") == 8.0
    assert chain.get_param("limiter", "true_peak_margin_db") == 1.0


def test_underscore_metadata_keys_are_tolerated_and_kept(cfg, tmp_path):
    extended = copy.deepcopy(cfg)
    extended["_calibration"] = {"measurements": {"noise_floor_dbfs": -52.3}, "reasons": {"x": "y"}}
    validate_config(extended)
    path = tmp_path / "meta.yaml"
    save_config(extended, path)
    assert load_config(path) == extended
    assert isinstance(Chain.from_config(extended), Chain)
    with pytest.raises(ConfigError, match="calibration: unknown key"):
        validate_config({**cfg, "calibration": {}})


def test_load_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("chain: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML syntax error"):
        load_config(broken)
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("42", encoding="utf-8")
    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_config(scalar)


def test_save_rejects_invalid_config(cfg, tmp_path):
    bad = copy.deepcopy(cfg)
    bad["chain"]["limiter"]["ceiling_dbfs"] = 5.0
    with pytest.raises(ConfigError):
        save_config(bad, tmp_path / "bad.yaml")
    assert not (tmp_path / "bad.yaml").exists()


def test_saved_file_is_plain_yaml(cfg, tmp_path):
    path = tmp_path / "plain.yaml"
    save_config(cfg, path)
    with path.open(encoding="utf-8") as fh:
        assert yaml.safe_load(fh) == cfg
