"""report_sync's mysql call must name the auth_socket user explicitly.

`ktpreports` authenticates by auth_socket and has no `.my.cnf`. Without
`--user`, the client sends `root` even under `sudo -u ktpreports`, and the
server refuses with 1698. report_service already sends the flag; these tests
hold report_sync to the same invocation.
"""
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts import report_service, report_sync


def _completed(stdout="ok\n"):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout,
                                       stderr="")


class ReportSyncMysqlUser(unittest.TestCase):

    def _argv(self, run):
        self.assertEqual(run.call_count, 1)
        return run.call_args.args[0]

    def test_sends_the_effective_user(self):
        with mock.patch("os.geteuid", create=True, return_value=4242), \
                mock.patch("pwd.getpwuid",
                           return_value=SimpleNamespace(pw_name="ktpreports")) as getpwuid, \
                mock.patch.object(report_sync.subprocess, "run",
                                  return_value=_completed()) as run:
            self.assertEqual(report_sync.mysql("SELECT 1"), "ok\n")

        getpwuid.assert_called_once_with(4242)
        argv = self._argv(run)
        self.assertIn("--user=ktpreports", argv)
        self.assertLess(argv.index("--user=ktpreports"),
                        argv.index(report_sync.DATABASE))

    def test_unresolvable_uid_fails_before_running_mysql(self):
        with mock.patch("os.geteuid", create=True, return_value=4242), \
                mock.patch("pwd.getpwuid",
                           side_effect=KeyError("getpwuid(): uid not found: 4242")), \
                mock.patch.object(report_sync.subprocess, "run") as run:
            with self.assertRaises(KeyError):
                report_sync.mysql("SELECT 1")
        run.assert_not_called()

    def test_matches_report_service_invocation(self):
        with mock.patch("os.geteuid", create=True, return_value=4242), \
                mock.patch("pwd.getpwuid",
                           return_value=SimpleNamespace(pw_name="ktpreports")), \
                mock.patch("subprocess.run", return_value=_completed()) as run:
            report_sync.mysql("SELECT 1")
            report_service.LocalMysql().sql("SELECT 1")

        self.assertEqual(run.call_count, 2)
        sync_argv, service_argv = (c.args[0] for c in run.call_args_list)
        self.assertEqual(sync_argv, service_argv)


if __name__ == "__main__":
    unittest.main()
