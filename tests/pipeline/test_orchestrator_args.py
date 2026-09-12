"""Host tests for orchestrator._parse_args — the new --target-clips/--recipe
flags build_main_cmd threads through from the API layer."""
from clippyme.pipeline.orchestrator import _parse_args


def test_target_clips_defaults_to_none():
    args = _parse_args(["-u", "https://x.com/v", "-o", "out"])
    assert args.target_clips is None


def test_target_clips_parsed():
    args = _parse_args(["-u", "https://x.com/v", "-o", "out", "--target-clips", "5"])
    assert args.target_clips == 5


def test_recipe_defaults_to_none():
    args = _parse_args(["-u", "https://x.com/v", "-o", "out"])
    assert args.recipe is None


def test_recipe_parsed_as_raw_json_string():
    raw = '{"toggles": {"hook": true}, "hook_params": {"position": "top"}}'
    args = _parse_args(["-u", "https://x.com/v", "-o", "out", "--recipe", raw])
    assert args.recipe == raw
