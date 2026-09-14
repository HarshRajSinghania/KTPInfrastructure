"""Fixture tests for scripts/fix-grub-default-kernel.sh.

tests/fixtures/grub_default_kernel/ holds GRUB files copied from game hosts
before and after the lowlatency flavour pin, with filesystem UUIDs replaced by
placeholders and grub.cfg trimmed to the header's default logic, the 10_linux
menu and the EFI firmware entry. The synthetic cases build menus in code.

Every path the script reads or writes is overridden into tmp_path, and
update-grub is a stub, so no test touches a real /etc or /boot.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "fix-grub-default-kernel.sh"
FIXTURES = ROOT / "tests" / "fixtures" / "grub_default_kernel"
# CI runs plain bash; on a Windows workstation "bash" resolves to WSL's, which
# cannot see these paths -- point KTP_TEST_BASH at Git Bash there.
BASH = os.environ.get("KTP_TEST_BASH", "bash")

DROPIN = "99-ktp-kernel-flavour.cfg"
DROPIN_BODY = (
    "# Rank lowlatency kernels above generic so entry 0 is the newest lowlatency kernel.\n"
    'GRUB_FLAVOUR_ORDER="lowlatency"\n'
)
ID_PIN = "gnulinux-advanced-ROOT_FS_UUID>gnulinux-6.8.0-138-lowlatency-advanced-ROOT_FS_UUID"
TITLE_PIN = "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-138-lowlatency"


def write(path: Path, text: str) -> None:
    path.write_text(text, newline="\n")


class Host:
    def __init__(self, root: Path):
        self.root = root
        self.cfg = root / "grub.cfg"
        self.default = root / "default-grub"
        self.grub_d = root / "grub.d"
        self.env = root / "grubenv"
        self.boot = root / "boot"
        self.backup = root / "backup"
        self.sort_version = root / "grub-sort-version"
        self.update_grub = root / "update-grub"
        for d in (self.grub_d, self.boot, self.backup):
            d.mkdir(parents=True, exist_ok=True)
        write(self.sort_version, 'for flavour in os.environ.get("GRUB_FLAVOUR_ORDER", "").split():\n')
        self.stub_update_grub("echo 'update-grub must not run in this test' >&2\nexit 99\n")

    def stub_update_grub(self, body: str) -> None:
        write(self.update_grub, "#!/bin/bash\n" + body)
        self.update_grub.chmod(0o755)

    def set_kernels(self, kernels: list[str]) -> None:
        for p in self.boot.glob("vmlinuz-*"):
            p.unlink()
        for k in kernels:
            write(self.boot / f"vmlinuz-{k}", "")

    def edit(self, path: Path, old: str, new: str) -> None:
        text = path.read_text()
        assert old in text, f"{old!r} not in {path}"
        write(path, text.replace(old, new))

    def run(self, *args: str, uname: str = "6.8.0-138-lowlatency"):
        return subprocess.run(
            [BASH, SCRIPT.as_posix(), *args],
            env={
                **os.environ,
                "KTP_GRUB_CFG": self.cfg.as_posix(),
                "KTP_GRUB_DEFAULT_FILE": self.default.as_posix(),
                "KTP_GRUB_DEFAULT_DIR": self.grub_d.as_posix(),
                "KTP_GRUB_ENV": self.env.as_posix(),
                "KTP_BOOT_DIR": self.boot.as_posix(),
                "KTP_GRUB_SORT_VERSION": self.sort_version.as_posix(),
                "KTP_UPDATE_GRUB": self.update_grub.as_posix(),
                "KTP_GRUB_BACKUP_DIR": self.backup.as_posix(),
                "KTP_UNAME_R": uname,
            },
            capture_output=True, text=True,
        )


def from_fixture(tmp_path: Path, name: str) -> Host:
    host = Host(tmp_path / name)
    src = FIXTURES / name
    shutil.copy(src / "grub.cfg", host.cfg)
    shutil.copy(src / "default-grub", host.default)
    shutil.copy(src / "grubenv", host.env)
    for f in (src / "grub.d").glob("*.cfg"):
        shutil.copy(f, host.grub_d / f.name)
    host.set_kernels((src / "kernels").read_text().split())
    return host


def synthetic(tmp_path: Path, default: str, kernels: list[str], etc_default: str,
              grubenv: str | None, boot_kernels: list[str]) -> Host:
    """A menu in 10_linux's shape: 'Ubuntu' boots kernels[0], then the Advanced submenu."""
    host = Host(tmp_path / "synthetic")
    cfg = (
        'if [ "${next_entry}" ] ; then\n   set default="${next_entry}"\nelse\n'
        f'   set default="{default}"\nfi\n'
        "menuentry 'Ubuntu' --class ubuntu $menuentry_id_option 'gnulinux-simple-x' {\n"
        f"\tlinux\t/boot/vmlinuz-{kernels[0]} root=UUID=x ro\n}}\n"
        "submenu 'Advanced options for Ubuntu' $menuentry_id_option 'gnulinux-advanced-x' {\n"
    )
    for k in kernels:
        cfg += (f"\tmenuentry 'Ubuntu, with Linux {k}' --class ubuntu $menuentry_id_option 'gnulinux-{k}-advanced-x' {{\n"
                f"\t\tlinux\t/boot/vmlinuz-{k} root=UUID=x ro\n\t}}\n"
                f"\tmenuentry 'Ubuntu, with Linux {k} (recovery mode)' --class ubuntu $menuentry_id_option 'gnulinux-{k}-recovery-x' {{\n"
                f"\t\tlinux\t/boot/vmlinuz-{k} root=UUID=x ro recovery nomodeset\n\t}}\n")
    cfg += "}\n"
    write(host.cfg, cfg)
    write(host.default, etc_default)
    if grubenv is not None:
        write(host.env, grubenv)
    host.set_kernels(boot_kernels)
    return host


def saved_one_zero(host: Host) -> Host:
    """Convert a GRUB_DEFAULT=0 host to the old saved + saved_entry=1>0 canon."""
    host.edit(host.cfg, 'set default="0"', 'set default="${saved_entry}"')
    host.edit(host.default, "GRUB_DEFAULT=0", "GRUB_DEFAULT=saved")
    write(host.env, "# GRUB Environment Block\nsaved_entry=1>0\n")
    return host


def pin(host: Host, spec: str) -> Host:
    host.edit(host.cfg, 'set default="0"', f'set default="{spec}"')
    host.edit(host.default, "GRUB_DEFAULT=0", f'GRUB_DEFAULT="{spec}"')
    return host


# --- measured fleet states ---------------------------------------------------

@pytest.mark.parametrize("name", ["atlanta-pre", "chicago-pre"])
def test_before_the_pin_entry0_is_the_newer_generic_kernel(tmp_path, name):
    """139-generic + 138-lowlatency with GRUB_DEFAULT=0: version order puts generic first."""
    r = from_fixture(tmp_path, name).run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "default entry    : 0 -> 'Ubuntu' -> 6.8.0-139-generic" in r.stdout
    assert "the default entry boots 6.8.0-139-generic, not the newest lowlatency kernel (6.8.0-138-lowlatency)" in r.stdout
    assert "WARN: non-lowlatency kernels are installed and lowlatency is not ranked first" in r.stdout
    assert "positional literal" not in r.stdout


def test_denver_lowlatency_only_passes(tmp_path):
    """No generic kernel installed, no drop-in, a separate /boot: entry 0 is already right."""
    r = from_fixture(tmp_path, "denver").run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "default entry    : 0 -> 'Ubuntu' -> 6.8.0-138-lowlatency" in r.stdout
    assert "next update-grub : entry 0 -> 6.8.0-138-lowlatency" in r.stdout
    assert "FINDING" not in r.stdout and "WARN" not in r.stdout
    assert "OK: the default boot entry is the newest lowlatency kernel" in r.stdout


def test_after_the_pin_passes(tmp_path):
    r = from_fixture(tmp_path, "atlanta-post").run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "flavour order    : lowlatency" in r.stdout
    assert DROPIN in r.stdout
    assert "default entry    : 0 -> 'Ubuntu' -> 6.8.0-138-lowlatency" in r.stdout
    assert "next update-grub : entry 0 -> 6.8.0-138-lowlatency" in r.stdout
    assert "FINDING" not in r.stdout and "WARN" not in r.stdout


def test_the_pin_holds_when_a_newer_generic_kernel_lands(tmp_path):
    host = from_fixture(tmp_path, "atlanta-post")
    host.set_kernels(["6.8.0-138-lowlatency", "6.8.0-139-generic", "6.8.0-140-generic"])
    r = host.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "next update-grub : entry 0 -> 6.8.0-138-lowlatency" in r.stdout


def test_a_newer_lowlatency_kernel_is_expected_at_entry0(tmp_path):
    host = from_fixture(tmp_path, "atlanta-post")
    host.set_kernels(["6.8.0-138-lowlatency", "6.8.0-139-generic", "6.8.0-139-lowlatency"])
    r = host.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "boots 6.8.0-138-lowlatency, not the newest lowlatency kernel (6.8.0-139-lowlatency)" in r.stdout
    assert "running update-grub fixes the default" in r.stdout


def test_dropin_without_update_grub_says_to_run_it(tmp_path):
    host = from_fixture(tmp_path, "atlanta-pre")
    write(host.grub_d / DROPIN, DROPIN_BODY)
    r = host.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "boots 6.8.0-139-generic, not the newest lowlatency kernel" in r.stdout
    assert "next update-grub : entry 0 -> 6.8.0-138-lowlatency" in r.stdout
    assert "running update-grub fixes the default" in r.stdout
    assert "WARN: non-lowlatency" not in r.stdout


def test_removing_the_dropin_rearms_generic_at_the_next_update_grub(tmp_path):
    host = from_fixture(tmp_path, "atlanta-post")
    (host.grub_d / DROPIN).unlink()
    r = host.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "default entry    : 0 -> 'Ubuntu' -> 6.8.0-138-lowlatency" in r.stdout
    assert "the next update-grub (any kernel install runs one) makes entry 0 6.8.0-139-generic, not 6.8.0-138-lowlatency" in r.stdout


def test_grub_without_the_flavour_patch_is_caught(tmp_path):
    host = from_fixture(tmp_path, "atlanta-post")
    write(host.sort_version, "# a grub-sort-version that ranks by version only\n")
    r = host.run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "makes entry 0 6.8.0-139-generic, not 6.8.0-138-lowlatency" in r.stdout
    assert "does not implement it; entry 0 follows version order" in r.stdout


def test_the_old_saved_one_zero_canon_boots_generic(tmp_path):
    """'1>0' is the Advanced submenu's first entry, which is ordered by version too."""
    r = saved_one_zero(from_fixture(tmp_path, "atlanta-pre")).run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "default entry    : 1>0 -> 'Ubuntu, with Linux 6.8.0-139-generic' -> 6.8.0-139-generic" in r.stdout
    assert "boots 6.8.0-139-generic, not the newest lowlatency kernel" in r.stdout
    assert "WARN: GRUB_DEFAULT=saved depends on grubenv" in r.stdout


@pytest.mark.parametrize("spec,label", [(ID_PIN, "menu-id pin"), (TITLE_PIN, "LITERAL TITLE pin")])
def test_a_kernel_pin_fails_even_while_it_boots_the_right_kernel(tmp_path, spec, label):
    r = pin(from_fixture(tmp_path, "atlanta-post"), spec).run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"the default is a {label}: '{spec}'" in r.stdout
    assert "-> 'Ubuntu, with Linux 6.8.0-138-lowlatency' -> 6.8.0-138-lowlatency" in r.stdout
    assert "next update-grub" not in r.stdout


def test_a_pending_one_shot_is_reported_not_failed(tmp_path):
    host = from_fixture(tmp_path, "atlanta-post")
    write(host.env, f"# GRUB Environment Block\nnext_entry={ID_PIN}\n")
    r = host.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"WARN: one-shot next_entry='{ID_PIN}' boots 6.8.0-138-lowlatency once at the next reboot" in r.stdout


# --- synthetic shapes from the 2026-08-25 audit --------------------------------

def test_saved_title_pin_on_an_old_kernel(tmp_path):
    """The incident shape: saved_entry pins the old kernel by menu title."""
    r = synthetic(
        tmp_path, "${saved_entry}",
        ["6.8.0-138-lowlatency", "6.8.0-138-generic", "6.8.0-110-lowlatency"],
        "GRUB_DEFAULT=saved\n",
        "# GRUB Environment Block\nsaved_entry=Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-110-lowlatency\n",
        ["6.8.0-110-lowlatency", "6.8.0-138-generic", "6.8.0-138-lowlatency"],
    ).run(uname="6.8.0-110-lowlatency")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "LITERAL TITLE pin" in r.stdout
    assert "boots 6.8.0-110-lowlatency, not the newest lowlatency kernel (6.8.0-138-lowlatency)" in r.stdout


def test_saved_one_zero_passes_only_within_one_abi(tmp_path):
    """138-generic + 138-lowlatency: the only kernel set where '1>0' is lowlatency."""
    r = synthetic(
        tmp_path, "${saved_entry}", ["6.8.0-138-lowlatency", "6.8.0-138-generic"],
        "GRUB_DEFAULT=saved\n", "# GRUB Environment Block\nsaved_entry=1>0\n",
        ["6.8.0-138-generic", "6.8.0-138-lowlatency"],
    ).run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FINDING" not in r.stdout
    assert "default entry    : 1>0 -> 'Ubuntu, with Linux 6.8.0-138-lowlatency' -> 6.8.0-138-lowlatency" in r.stdout
    assert "WARN: non-lowlatency kernels are installed" in r.stdout


def test_etc_default_positional_disagrees_with_baked(tmp_path):
    """GRUB_DEFAULT '1>2' (the old kernel) while grub.cfg still bakes '1>0'."""
    r = synthetic(
        tmp_path, "1>0", ["6.8.0-138-lowlatency", "6.8.0-110-lowlatency"],
        'GRUB_DEFAULT="1>2"\n', None, ["6.8.0-110-lowlatency", "6.8.0-138-lowlatency"],
    ).run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "GRUB_DEFAULT is a positional literal ('1>2')" in r.stdout
    assert "disagrees with the baked grub.cfg" in r.stdout
    assert "default entry    : 1>0 -> 'Ubuntu, with Linux 6.8.0-138-lowlatency' -> 6.8.0-138-lowlatency" in r.stdout


def test_baked_positional_lands_on_generic(tmp_path):
    r = synthetic(
        tmp_path, "1>2", ["6.8.0-138-lowlatency", "6.8.0-138-generic"],
        'GRUB_DEFAULT="1>2"\n', None, ["6.8.0-138-generic", "6.8.0-138-lowlatency"],
    ).run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "the default is a positional literal ('1>2')" in r.stdout
    assert "disagrees" not in r.stdout
    assert "boots 6.8.0-138-generic, not the newest lowlatency kernel" in r.stdout


def test_a_recovery_entry_default_is_a_finding(tmp_path):
    r = synthetic(
        tmp_path, "1>1", ["6.8.0-138-lowlatency"], 'GRUB_DEFAULT="1>1"\n', None, ["6.8.0-138-lowlatency"],
    ).run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "recovery-mode entry" in r.stdout


@pytest.mark.parametrize("bad", ["", "garbage>9"])
def test_unresolvable_default_is_a_finding_not_a_pass(tmp_path, bad):
    """A zero must not read as clean: an unresolvable default still exits 1."""
    r = synthetic(
        tmp_path, bad or "9>9", ["6.8.0-138-lowlatency"], 'GRUB_DEFAULT="9>9"\n', None, ["6.8.0-138-lowlatency"],
    ).run()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "could not resolve" in r.stdout


# --- --fix ----------------------------------------------------------------------

def test_fix_installs_the_dropin_runs_update_grub_and_reaudits_clean(tmp_path):
    host = from_fixture(tmp_path, "atlanta-pre")
    before = {p: p.read_bytes() for p in (host.cfg, host.default, host.env)}
    post_cfg = (FIXTURES / "atlanta-post" / "grub.cfg").as_posix()
    # Like the real update-grub, the stub only reorders the menu if it can source the drop-in.
    host.stub_update_grub(
        f'grep -qx \'GRUB_FLAVOUR_ORDER="lowlatency"\' "$KTP_GRUB_DEFAULT_DIR/{DROPIN}" || exit 1\n'
        f'cp "{post_cfg}" "$KTP_GRUB_CFG"\necho ran >> "$KTP_GRUB_BACKUP_DIR/stub-ran"\n'
    )
    r = host.run("--fix")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (host.grub_d / DROPIN).read_bytes() == DROPIN_BODY.encode()
    assert (host.backup / "stub-ran").read_text() == "ran\n"
    backups = list(host.backup.glob("grub.cfg.pre-flavour-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before[host.cfg]
    assert host.default.read_bytes() == before[host.default]
    assert host.env.read_bytes() == before[host.env]
    assert "OK: the default boot entry is the newest lowlatency kernel" in r.stdout
    assert "DONE." in r.stdout


def test_fix_is_a_noop_on_a_pinned_host(tmp_path):
    host = from_fixture(tmp_path, "atlanta-post")
    r = host.run("--fix")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--fix: nothing to do" in r.stdout
    assert not any(host.backup.iterdir())


def test_fix_stops_when_update_grub_fails(tmp_path):
    host = from_fixture(tmp_path, "atlanta-pre")
    r = host.run("--fix")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "FATAL:" in r.stderr and "grub.cfg backup" in r.stderr
    assert "DONE." not in r.stdout


@pytest.mark.parametrize("shape,reason", [
    ("saved", "GRUB_DEFAULT is 'saved', not 0"),
    ("id-pin", "the default pins a kernel"),
    ("unpatched-grub", "does not implement GRUB_FLAVOUR_ORDER"),
    ("foreign-flavour-order", "GRUB_FLAVOUR_ORDER='generic' is set in"),
])
def test_fix_refuses_shapes_it_cannot_reason_about(tmp_path, shape, reason):
    host = from_fixture(tmp_path, "atlanta-pre")
    if shape == "saved":
        saved_one_zero(host)
    elif shape == "id-pin":
        pin(host, ID_PIN.replace("138-lowlatency", "139-generic"))
    elif shape == "unpatched-grub":
        write(host.sort_version, "# ranks by version only\n")
    else:
        write(host.grub_d / "50-other.cfg", 'GRUB_FLAVOUR_ORDER="generic"\n')
    r = host.run("--fix")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "REFUSING --fix" in r.stderr and reason in r.stderr
    assert not (host.grub_d / DROPIN).exists()
    assert not any(host.backup.iterdir())
