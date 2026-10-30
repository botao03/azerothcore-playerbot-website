"""AzerothCore 3.3.5a SRP6 credential format, as defined by SRP6.cpp."""

import hashlib
import hmac
import secrets

MODULUS = int("894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7", 16)


def calculate_verifier(username, password, salt):
    identity = f"{username.upper()}:{password.upper()}".encode("ascii")
    identity_hash = hashlib.sha1(identity).digest()
    exponent = int.from_bytes(hashlib.sha1(salt + identity_hash).digest(), "little")
    return pow(7, exponent, MODULUS).to_bytes(32, "little")


def registration_data(username, password):
    salt = secrets.token_bytes(32)
    return salt, calculate_verifier(username, password, salt)


def check_password(username, password, salt, verifier):
    return hmac.compare_digest(calculate_verifier(username, password, salt), verifier)
