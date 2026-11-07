"""Tests for dupfind, using tmp_path fixtures throughout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from dupfind.cli import format_size, main, parse_size
from dupfind.core import find_duplicates, hash_file


def write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# --- core behaviour -------------------------------------------------------


def test_identical_files_in_different_dirs_are_grouped(tmp_path):
    write(tmp_path / "a" / "photo.jpg", b"fake-jpeg-bytes" * 10)
    write(tmp_path / "b" / "copy.jpg", b"fake-jpeg-bytes" * 10)
    write(tmp_path / "b" / "other.txt", b"something else entirely")

    result = find_duplicates([tmp_path])

    assert result.files_scanned == 3
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.size == len(b"fake-jpeg-bytes" * 10)
    assert group.sha256 == hash_file(tmp_path / "a" / "photo.jpg")
    assert {p.name for p in group.paths} == {"photo.jpg", "copy.jpg"}
    assert result.duplicate_files == 1
    assert result.bytes_reclaimable == group.size


def test_same_size_different_content_is_not_flagged(tmp_path):
    write(tmp_path / "one.txt", b"AAAAAAAAAA")
    write(tmp_path / "two.txt", b"BBBBBBBBBB")  # same 10 bytes, other content

    result = find_duplicates([tmp_path])

    assert result.groups == []
    assert result.duplicate_files == 0
    assert result.bytes_reclaimable == 0


def test_nested_directories_are_scanned(tmp_path):
    write(tmp_path / "deep" / "deeper" / "x.bin", b"\x00\x01" * 50)
    write(tmp_path / "deep" / "y.bin", b"\x00\x01" * 50)
    write(tmp_path / "z.bin", b"unique")

    result = find_duplicates([tmp_path])

    assert result.files_scanned == 3
    assert len(result.groups) == 1
    assert len(result.groups[0].paths) == 2


def test_empty_files_count_as_duplicates(tmp_path):
    write(tmp_path / "empty1.txt", b"")
    write(tmp_path / "empty2.txt", b"")
    write(tmp_path / "nonempty.txt", b"x")

    result = find_duplicates([tmp_path])

    assert len(result.groups) == 1
    assert result.groups[0].size == 0
    assert result.duplicate_files == 1


def test_empty_directory_reports_no_duplicates(tmp_path):
    result = find_duplicates([tmp_path])

    assert result.files_scanned == 0
    assert result.groups == []
    assert result.duplicate_files == 0
    assert result.bytes_reclaimable == 0


def test_min_size_filters_small_files(tmp_path):
    write(tmp_path / "small1.txt", b"tiny")
    write(tmp_path / "small2.txt", b"tiny")
    write(tmp_path / "big1.txt", b"a" * 100)
    write(tmp_path / "big2.txt", b"a" * 100)

    result = find_duplicates([tmp_path], min_size=10)

    assert result.files_scanned == 2
    assert len(result.groups) == 1
    assert result.groups[0].paths[0].name.startswith("big")


def test_exclude_patterns_skip_files_and_dirs(tmp_path):
    write(tmp_path / "keep1.txt", b"data" * 10)
    write(tmp_path / "keep2.txt", b"data" * 10)
    write(tmp_path / "skip1.tmp", b"data" * 10)
    write(tmp_path / "skip2.tmp", b"data" * 10)
    write(tmp_path / "cache" / "c1.txt", b"data" * 10)
    write(tmp_path / "cache" / "c2.txt", b"data" * 10)

    result = find_duplicates([tmp_path], exclude=["*.tmp", "cache"])

    assert result.files_scanned == 2
    assert len(result.groups) == 1
    assert {p.name for p in result.groups[0].paths} == {"keep1.txt", "keep2.txt"}


def test_multiple_roots_are_combined(tmp_path):
    write(tmp_path / "dir1" / "a.txt", b"shared")
    write(tmp_path / "dir2" / "b.txt", b"shared")

    result = find_duplicates([tmp_path / "dir1", tmp_path / "dir2"])

    assert len(result.groups) == 1
    assert result.duplicate_files == 1


def test_symlinks_skipped_by_default_and_followed_on_request(tmp_path):
    target = write(tmp_path / "real.txt", b"content" * 20)
    write(tmp_path / "copy.txt", b"content" * 20)
    os.symlink(target, tmp_path / "link.txt")

    skipped = find_duplicates([tmp_path])
    assert skipped.files_scanned == 2
    assert len(skipped.groups) == 1

    followed = find_duplicates([tmp_path], follow_symlinks=True)
    assert followed.files_scanned == 3
    assert len(followed.groups) == 1
    assert len(followed.groups[0].paths) == 3


def test_unreadable_file_warns_and_does_not_crash(tmp_path, monkeypatch):
    # Simulate an unreadable file by making hashing fail for it: chmod-based
    # unreadability is not observable when tests run as root, so force the
    # failure instead — this exercises the exact warning path in core.py.
    import dupfind.core as core

    write(tmp_path / "good1.txt", b"readable" * 5)
    write(tmp_path / "good2.txt", b"readable" * 5)
    locked = write(tmp_path / "locked.txt", b"readable" * 5)
    real_hash = core.hash_file

    def flaky_hash(path):
        if path == locked:
            raise PermissionError("permission denied")
        return real_hash(path)

    monkeypatch.setattr(core, "hash_file", flaky_hash)

    result = find_duplicates([tmp_path])

    assert len(result.groups) == 1  # the two readable copies still group
    assert any("locked.txt" in w for w in result.warnings)


def test_missing_path_warns_instead_of_crashing(tmp_path):
    result = find_duplicates([tmp_path / "does-not-exist"])

    assert result.files_scanned == 0
    assert result.groups == []
    assert len(result.warnings) == 1


def test_three_copies_reclaim_two_copies_worth(tmp_path):
    for name in ("a.txt", "b.txt", "c.txt"):
        write(tmp_path / name, b"z" * 64)

    result = find_duplicates([tmp_path])

    assert result.duplicate_files == 2
    assert result.bytes_reclaimable == 128


# --- helpers --------------------------------------------------------------


def test_parse_size_suffixes():
    assert parse_size("1024") == 1024
    assert parse_size("10K") == 10 * 1024
    assert parse_size("5MB") == 5 * 1024**2
    assert parse_size("1g") == 1024**3
    assert parse_size("0") == 0


def test_parse_size_rejects_garbage():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size("ten")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size("-5")


def test_format_size():
    assert format_size(0) == "0 B"
    assert format_size(512) == "512 B"
    assert format_size(1536) == "1.5 KiB"
    assert format_size(2 * 1024**2) == "2.0 MiB"


# --- CLI ------------------------------------------------------------------


def test_cli_no_duplicates_exit_0(tmp_path, capsys):
    write(tmp_path / "a.txt", b"one")
    write(tmp_path / "b.txt", b"two")

    assert main([str(tmp_path)]) == 0
    assert "No duplicates found." in capsys.readouterr().out


def test_cli_duplicates_exit_1(tmp_path, capsys):
    write(tmp_path / "a.txt", b"same content")
    write(tmp_path / "b.txt", b"same content")

    assert main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "1 duplicate group(s)" in out
    assert "1 duplicate file(s)" in out
    assert "12 B reclaimable" in out


def test_cli_missing_path_exit_2(tmp_path, capsys):
    assert main([str(tmp_path / "nope")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_cli_dry_run_flag_without_delete_is_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--dry-run", "."])
    assert excinfo.value.code == 2


def test_cli_json_output_shape(tmp_path, capsys):
    write(tmp_path / "a.txt", b"dup" * 10)
    write(tmp_path / "b.txt", b"dup" * 10)
    write(tmp_path / "c.txt", b"unique content here")

    assert main(["--json", str(tmp_path)]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["files_scanned"] == 3
    assert payload["duplicate_files"] == 1
    assert payload["bytes_reclaimable"] == 30
    assert payload["warnings"] == []
    assert len(payload["duplicate_groups"]) == 1
    group = payload["duplicate_groups"][0]
    assert group["size"] == 30
    assert group["sha256"] == hash_file(tmp_path / "a.txt")
    assert sorted(Path(p).name for p in group["files"]) == ["a.txt", "b.txt"]


def test_cli_json_no_duplicates_shape(tmp_path, capsys):
    write(tmp_path / "a.txt", b"one")

    assert main(["--json", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["duplicate_groups"] == []
    assert payload["duplicate_files"] == 0
    assert payload["bytes_reclaimable"] == 0


def test_cli_dry_run_deletes_nothing(tmp_path, capsys):
    first = write(tmp_path / "a.txt", b"dup" * 10)
    second = write(tmp_path / "b.txt", b"dup" * 10)

    assert main(["--delete", "--dry-run", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "Would delete" in out
    assert first.exists() and second.exists()


def test_cli_delete_keeps_first_copy_on_yes(tmp_path, monkeypatch, capsys):
    write(tmp_path / "a.txt", b"dup" * 10)
    write(tmp_path / "b.txt", b"dup" * 10)
    write(tmp_path / "c.txt", b"dup" * 10)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    assert main(["--delete", str(tmp_path)]) == 1
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == ["a.txt"]  # sorted first copy kept, the rest deleted


def test_cli_delete_declined_keeps_everything(tmp_path, monkeypatch):
    write(tmp_path / "a.txt", b"dup" * 10)
    write(tmp_path / "b.txt", b"dup" * 10)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    assert main(["--delete", str(tmp_path)]) == 1
    assert len(list(tmp_path.iterdir())) == 2
