import os
import unittest
from unittest.mock import patch

from app.account_runner import account_workdir


class AccountRunnerTests(unittest.TestCase):
    def test_workdir_is_independent_from_remote_authority_root(self):
        with patch.dict(
            os.environ,
            {
                "CODEX_PROJECT_WORKDIR": "/srv/local-implementation",
                "PROJECT_SOURCE_ROOT": "/srv/remote-authority",
            },
        ):
            self.assertEqual(account_workdir(), "/srv/local-implementation")


if __name__ == "__main__":
    unittest.main()
