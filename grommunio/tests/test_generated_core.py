"""Exercise certificate preservation in a freshly generated build context.

Set GROMMUNIO_TEST_CORE_DIR to the generated gromox-core directory. The tests
use temporary certificates and stub trust-store updates; no Docker or root needed.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class CertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        generated = os.environ.get("GROMMUNIO_TEST_CORE_DIR")
        if not generated:
            raise unittest.SkipTest("Set GROMMUNIO_TEST_CORE_DIR to generated gromox-core")
        cls.source = (Path(generated) / "common/ssl_setup").read_text()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "anchors").mkdir()
        self.helper = self.root / "ssl_setup"
        self.helper.write_text(self.source.replace("/etc/pki/trust/anchors/", str(self.root / "anchors") + "/"))
        self.cert = self.root / "server-bundle.pem"
        self.key = self.root / "server.key"

    def invoke(self):
        subprocess.run([
            "bash", "-ec",
            '. "$1"; SSL_BUNDLE_T="$2"; SSL_KEY_T="$3"; LOGFILE="$4"; '
            'FQDN=mail.example.invalid; DOMAIN=example.invalid; SSL_DAYS=30; '
            'update-ca-certificates() { :; }; selfcert',
            "test", str(self.helper), str(self.cert), str(self.key), str(self.root / "setup.log"),
        ], check=True, capture_output=True)

    def fingerprint(self):
        return subprocess.check_output([
            "openssl", "x509", "-in", str(self.cert), "-noout", "-fingerprint", "-sha256",
        ])

    def assert_matching_key(self):
        cert_key = subprocess.check_output(["openssl", "x509", "-in", str(self.cert), "-pubkey", "-noout"])
        private_key = subprocess.check_output(["openssl", "pkey", "-in", str(self.key), "-pubout"])
        self.assertEqual(cert_key, private_key)

    def test_initial_generation(self):
        self.invoke()
        self.assert_matching_key()
        self.assertTrue((self.root / "anchors/server-bundle.pem").exists())

    def test_existing_certificate_and_key_survive_recreation(self):
        self.invoke()
        fingerprint, private_key = self.fingerprint(), self.key.read_bytes()
        self.invoke()
        self.assertEqual(fingerprint, self.fingerprint())
        self.assertEqual(private_key, self.key.read_bytes())

    def test_mismatched_existing_key_is_repaired(self):
        self.invoke()
        fingerprint = self.fingerprint()
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-out", str(self.key)],
                       check=True, capture_output=True)
        self.invoke()
        self.assertNotEqual(fingerprint, self.fingerprint())
        self.assert_matching_key()


if __name__ == "__main__":
    unittest.main()
