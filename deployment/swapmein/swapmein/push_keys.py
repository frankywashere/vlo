"""Create an operator-owned VAPID key pair without printing private material."""
import argparse
import base64
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    options.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    # Exclusive creation avoids accidentally rotating a deployed key pair.
    descriptor = os.open(options.out / "vapid-private.pem", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(private)
    with (options.out / "vapid-public.txt").open("x", encoding="ascii") as file:
        file.write(base64.urlsafe_b64encode(public).rstrip(b"=").decode("ascii"))


if __name__ == "__main__":
    main()
