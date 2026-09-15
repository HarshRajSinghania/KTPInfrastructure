#!/bin/bash
# One-shot: apt upgrade then reboot. Scheduled 2026-08-24 by operator request.
set -u
LOG=/var/log/ktp-kernel-update.log
exec >>"$LOG" 2>&1
echo "=== $(date -Is) START on $(hostname)"
echo "kernel running before: $(uname -r)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q || echo "WARN: apt-get update returned $?"
apt-get -y -o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-confdef upgrade
RC=$?
echo "apt upgrade rc=$RC"
echo "newest installed kernel: $(ls -1 /boot/vmlinuz-* 2>/dev/null | sed 's|.*vmlinuz-||' | sort -V | tail -1)"
if [ "$RC" -ne 0 ]; then
  echo "=== $(date -Is) ABORT: apt failed, NOT rebooting"
  exit 1
fi
echo "=== $(date -Is) rebooting now"
systemctl reboot
