# GRUB default-kernel audit and repair

Game hosts should boot the newest installed lowlatency kernel. This runbook covers how that is
pinned, how to check it without rebooting, how to undo it, and what can still go wrong.

## The pin

`GRUB_DEFAULT=0` in `/etc/default/grub`, plus one drop-in:

```
# /etc/default/grub.d/99-ktp-kernel-flavour.cfg
# Rank lowlatency kernels above generic so entry 0 is the newest lowlatency kernel.
GRUB_FLAVOUR_ORDER="lowlatency"
```

Nothing else: no `GRUB_DEFAULT=saved`, no grubenv entry, no index past entry 0, no title or id pin.

### Why version order is not enough

Ubuntu's `10_linux` sorts kernels by version first. Flavour only breaks a tie between kernels of the
same version. Ubuntu usually publishes a generic kernel a few days before the matching lowlatency
one, and any host with `linux-generic` installed pulls it in through unattended-upgrades. Until
lowlatency catches up, the newer generic kernel is entry 0 and also the first entry of the Advanced
submenu, and a reboot in that window boots generic without any error. (On noble both flavours are
built with `CONFIG_HZ=1000`; the visible difference is the default preemption model, `full` on
lowlatency and `voluntary` on generic.)

`GRUB_FLAVOUR_ORDER` is an Ubuntu grub patch. `grub-mkconfig` sources `/etc/default/grub.d/*.cfg`
after `/etc/default/grub` and exports the variable, and `/usr/lib/grub/grub-sort-version` ranks the
named flavours above everything else, newest first (grub2 `2.12-1ubuntu7` and later; the fleet runs
`2.12-1ubuntu7.3`).

| kernels in /boot | entry 0 by version order | entry 0 with lowlatency ranked first |
|---|---|---|
| 139-generic, 138-lowlatency | 139-generic | 138-lowlatency |
| 140-generic, 139-lowlatency, 139-generic, 138-lowlatency | 140-generic | 139-lowlatency |
| 139-lowlatency, 138-lowlatency | 139-lowlatency | 139-lowlatency |

Generic kernels stay installed and bootable from the Advanced submenu. Every kernel install runs
`update-grub`, which reads the drop-in again, so entry 0 moves to each new lowlatency kernel with no
per-update step.

## Where it is applied

Applied 2026-09-13 on Atlanta, Dallas and New York. Chicago still boots its newest generic kernel at
the next reboot and is waiting on console access. Denver has no generic kernel installed, so entry 0
is already right there. Check any host with the audit script below rather than trusting this list.

## Apply

As root. Not during `apt-daily-upgrade` (about 06:00-07:00 ET), and never pipe `update-grub`: a
closed pipe kills it partway through writing `grub.cfg`. The change is inert until the next reboot,
but it decides what that reboot boots, so apply it only where a failed boot is recoverable (console
access).

```bash
scripts/fix-grub-default-kernel.sh --fix
```

or by hand:

```bash
pgrep -a 'apt|dpkg|unattended-upgr' || echo no-apt-running
grep '^GRUB_DEFAULT=' /etc/default/grub                       # GRUB_DEFAULT=0
grub-editenv list                                             # empty
grep -c GRUB_FLAVOUR_ORDER /usr/lib/grub/grub-sort-version    # 1 or more; 0 means no patch, stop

cp -a /boot/grub/grub.cfg /root/grub.cfg.pre-flavour-$(date +%Y%m%d)
printf '%s\n' '# Rank lowlatency kernels above generic so entry 0 is the newest lowlatency kernel.' \
    'GRUB_FLAVOUR_ORDER="lowlatency"' > /etc/default/grub.d/99-ktp-kernel-flavour.cfg
update-grub > /root/update-grub.flavour-$(date +%Y%m%d-%H%M%S).log 2>&1; echo rc=$?
```

To rehearse without touching `/boot`, generate into a temp dir (run it from that dir, because
`05_debian_theme` globs relative to the working directory):

```bash
D=$(mktemp -d /tmp/ktpgrubsim.XXXXXX); cd "$D"
GRUB_FLAVOUR_ORDER=lowlatency grub-mkconfig -o "$D/sim.cfg" > "$D/sim.log" 2>&1; echo rc=$?
awk '/^menuentry /{f=1} f && /^\tlinux\t/{print $2; exit}' "$D/sim.cfg"
cd /; rm -rf "$D"
```

## Verify without rebooting

```bash
awk '/^menuentry /{f=1} f && /^\tlinux\t/{print $2; exit}' /boot/grub/grub.cfg
ls /boot/vmlinuz-*-lowlatency | sort -V | tail -1
```

Both lines must name the same kernel. (With a separate `/boot` partition the first prints
`/vmlinuz-...` without the `/boot` prefix; compare the version.) Then:

```bash
grep -n 'set default="0"' /boot/grub/grub.cfg      # present
grub-editenv list                                   # still empty
grub-script-check /boot/grub/grub.cfg && echo ok
scripts/fix-grub-default-kernel.sh                  # exit 0
```

## Rollback

```bash
rm /etc/default/grub.d/99-ktp-kernel-flavour.cfg
update-grub > /root/update-grub.flavour-rollback-$(date +%Y%m%d-%H%M%S).log 2>&1
```

With the same kernels in `/boot`, `grub.cfg` comes back byte for byte. Rollback also brings back the
problem: the newest kernel of any flavour is entry 0 again.

## Known risks

- **An untested kernel at the next reboot.** If a new lowlatency kernel installs between the pin
  and a reboot, entry 0 is a kernel this host has never booted. That is the intended behaviour, but
  look at `ls /boot/vmlinuz-*` before a planned reboot. To boot the known-good kernel for one boot
  only, use a one-shot by menu id:

  ```bash
  grep -o "gnulinux-advanced-[^']*\|gnulinux-[^']*-lowlatency-advanced-[^']*" /boot/grub/grub.cfg | sort -u
  grub-reboot 'gnulinux-advanced-<root-uuid>>gnulinux-<version>-lowlatency-advanced-<root-uuid>'
  grub-editenv list      # next_entry is set
  ```

  GRUB clears `next_entry` at that boot, and the boot after returns to entry 0. Chicago boots
  through Linode's GRUB, and whether that reads grubenv is unverified, so don't count on a one-shot
  there without the console.
- **The patch is Ubuntu's, not upstream GRUB's.** A grub2 update that dropped it would quietly
  return to version order. The audit script checks `grub-sort-version` and fails when the next
  `update-grub` would put anything but the newest lowlatency kernel at entry 0.
- **Lowlatency publication stalls or stops.** The pin keeps booting the newest lowlatency kernel
  while newer generic security kernels install and sit unused. Compare
  `apt-cache policy linux-image-lowlatency` with `linux-image-generic` from time to time. If
  lowlatency is dropped for the release, the pin needs replacing, not tuning.

## Audit and fix

```bash
scripts/fix-grub-default-kernel.sh          # read-only; exit 1 on findings, 2 if grub.cfg is unreadable
scripts/fix-grub-default-kernel.sh --fix    # drop-in + update-grub + re-audit; never reboots
```

The audit resolves the default entry the way GRUB does (`0`, `saved` through grubenv's
`saved_entry`, or a `>`-separated path of indexes, titles or ids) down to the `linux` line it boots,
and fails unless that kernel is the newest installed lowlatency kernel. It also predicts entry 0
after the next `update-grub` from the installed kernels, `GRUB_FLAVOUR_ORDER` and whether
`grub-sort-version` implements it, and fails if the answer changes. Other findings: a default that
names a kernel version (title or id pin, even while it boots the right kernel), an index past the
first entry, `/etc/default/grub` disagreeing with the baked `grub.cfg`, and a recovery-mode default.
Warnings, which don't fail the run: generic kernels installed without the flavour pin,
`GRUB_DEFAULT=saved`, and a pending one-shot `next_entry`.

`--fix` does nothing on a host that already passes with lowlatency ranked first. Otherwise it acts
only on `GRUB_DEFAULT=0` with a patched grub: it copies `grub.cfg` to `/root`, writes the drop-in,
runs `update-grub` into a log in `/root`, and re-runs the audit. It refuses `saved`, pins,
positional defaults, a pending one-shot, an unpatched grub, and a `GRUB_FLAVOUR_ORDER` set in some
other file. Convert those by hand to `GRUB_DEFAULT=0` first.

## Superseded: `GRUB_DEFAULT=saved` + `saved_entry=1>0`

Before 2026-09-13 this runbook called `saved` + `grub-set-default '1>0'` the fleet canon, reasoning
that grub-mkconfig emits kernels newest-first with `-lowlatency` ahead of `-generic`, so `1>0` (the
Advanced submenu's first entry) always tracks the newest lowlatency kernel. **That is wrong.**
Lowlatency only sorts ahead of generic when both have the same version. The claim held when it was
written because 138-generic and 138-lowlatency were installed together; with 139-generic and
138-lowlatency, `1>0` is 139-generic. The fleet had moved to `GRUB_DEFAULT=0` on 2026-08-28, which
fails the same way for the same reason, and the old audit script could not model it: it reported
every `GRUB_DEFAULT=0` host, including a correct one, as an unresolvable positional literal.

## History: the 2026-08-25 title pin

At the 2026-08-25 01:00 ET reboot, Atlanta came back on `6.8.0-110-lowlatency` while the other four
game hosts took `6.8.0-138`. Its grubenv held

```
saved_entry=Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-110-lowlatency
```

a pin by literal menu title, left behind by the `preempt=full` kernel experiment. Kernel updates
kept installing, but because the pinned kernel was the one running, `/run/reboot-required` cleared
at each reboot and every reboot looked successful. That is why the audit treats any title or id pin
as a finding. `provision/provision-gameserver.sh` wrote the same kind of title pin into
`GRUB_DEFAULT` until 2026-09-13.

The same audit found Dallas and New York on `saved` + `1>0` (right only because both flavours
shared a version), Denver with `GRUB_DEFAULT="1>2"` that the next `update-grub` would have baked into
a downgrade to `6.8.0-110`, and Chicago baked on `1>2`, which was `6.8.0-138-generic`. All five were
moved to `GRUB_DEFAULT=0` on 2026-08-28.
