#!/opt/omnidoer/omnidoer/.venv/bin/python
"""
Control-server helper: upload the MockTC source repo to the ECC host
(erphost, via FRP SSH tunnel 39.104.206.210:10022) and run scripts/deploy.sh.

Only the /oracle filesystem is used on the target host. The global python on
the ECC host is never modified: deploy.sh installs a standalone Python 3.11
under /oracle/python311 and creates a venv at /oracle/mocktc/venv.
"""

import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/omnidoer/omnidoer")

from omnidoer.omni_vault.vault import Vault


HOST = "39.104.206.210"
PORT = "10022"
CRED_ID = "cred_afb88c5ca9bf4a74be7b9a29f39743f5"  # ECC Server(port 10022)
REPO = Path(__file__).resolve().parent.parent


def ssh(secret, args, stdin_bytes=None, check=True):
    env = os.environ.copy()
    env["SSHPASS"] = secret.password
    cmd = [
        "sshpass",
        "-e",
        "ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=20",
        "-p",
        PORT,
        "%s@%s" % (secret.username, HOST),
    ] + args
    proc = subprocess.run(cmd, input=stdin_bytes, env=env, capture_output=True)
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stdout.decode("utf-8", "replace"))
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit("remote command failed: %s" % " ".join(args))
    return proc.stdout.decode("utf-8", "replace")


def main():
    secret = Vault.load(
        Path("/root/.omnidoer/vault.json"),
        Path("/root/.omnidoer/vault-passphrase").read_text().strip(),
    ).decrypt_credential(CRED_ID)

    tarball = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
    tarball.close()
    with tarfile.open(tarball.name, "w:gz") as tar:
        for rel in [
            "mocktc_app",
            "scripts",
            "systemd",
            "tests",
            "requirements.txt",
            "README.md",
            ".gitignore",
        ]:
            path = REPO / rel
            if path.exists():
                tar.add(path, arcname=rel)

    try:
        env = os.environ.copy()
        env["SSHPASS"] = secret.password
        subprocess.run(
            [
                "sshpass",
                "-e",
                "scp",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-P",
                PORT,
                tarball.name,
                "%s@%s:/oracle/mocktc-src.tar.gz" % (secret.username, HOST),
            ],
            env=env,
            check=True,
        )
        remote_script = (
            "set -e; mkdir -p /oracle/mocktc && "
            "tar -xzf /oracle/mocktc-src.tar.gz -C /oracle/mocktc && "
            "rm -f /oracle/mocktc-src.tar.gz && "
            "bash /oracle/mocktc/scripts/deploy.sh"
        )
        ssh(secret, ["bash", "-s"], stdin_bytes=remote_script.encode())
        print("==> deploy finished")
    finally:
        os.unlink(tarball.name)


if __name__ == "__main__":
    main()
